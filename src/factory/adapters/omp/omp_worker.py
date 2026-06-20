"""OmpWorker — NATS worker adapter that dispatches jobs to omp_rpc.

Subscribes to factory.jobs.omp (queue group omp-workers), translates
job envelopes into omp_rpc.RpcClient.prompt_and_wait() calls, and
publishes JobProgress / JobResult events back onto the bus.

Model B concurrency: one replica runs up to M jobs in parallel.
handle() spawns each job as an asyncio.Task and returns immediately,
freeing the dispatch loop. Concurrency is bounded by the pool's
internal Semaphore(M). Each pool worker carries its own bridge.

SanitizedError discipline (ADR-073): all bus-bound error message
fields carry type(exc).__name__ only — see _classify_exception in
_rpc_bridge.py.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Any

from factory.adapters.omp._rpc_bridge import publish_job_error
from factory.adapters.omp.omp_pool import OmpPool
from roxabi_contracts.jobs.models import JobEnvelope
from roxabi_contracts.jobs.subjects import jobs_submit
from roxabi_nats import nats_connect
from roxabi_nats.adapter_base import NatsAdapterBase

log = logging.getLogger(__name__)

# -----------------------------------------------------------------
# Module-level constants — single source of truth for this worker
# -----------------------------------------------------------------
_CMD_SUBJECT = jobs_submit("omp")  # "factory.jobs.omp"
_QUEUE_GROUP = "omp-workers"
_ENVELOPE_NAME = "JobEnvelope"
_SCHEMA_VERSION = 1
_HEARTBEAT_SUBJECT = "factory.omp.heartbeat"
_HEARTBEAT_INTERVAL = 30.0


class OmpWorker(NatsAdapterBase):
    """NATS worker that dispatches omp jobs via OmpPool (Model B concurrency).

    One OmpPool per worker process; the pool bounds concurrency via an
    internal Semaphore(M). handle() spawns each job as an asyncio.Task
    and returns immediately, freeing the dispatch loop for the next message.
    """

    def __init__(
        self,
        *,
        pool: OmpPool | None = None,
        timeout: float = 300.0,
        identity_name: str | None = None,
    ) -> None:
        super().__init__(
            subject=_CMD_SUBJECT,
            queue_group=_QUEUE_GROUP,
            envelope_name=_ENVELOPE_NAME,
            schema_version=_SCHEMA_VERSION,
            timeout=timeout,
            heartbeat_subject=_HEARTBEAT_SUBJECT,
            heartbeat_interval=_HEARTBEAT_INTERVAL,
            identity_name=identity_name,
            wait_ready=False,  # worker semantics — hub readiness not required
        )
        # Allow injection for testing; production always constructs real pool.
        self._pool: OmpPool = pool if pool is not None else OmpPool()
        self._jobs: set[asyncio.Task] = set()
        self._nc: Any | None = None

    # ------------------------------------------------------------------
    # Lifecycle override — connect then register pool before entering
    # the main subscription loop.
    # ------------------------------------------------------------------

    async def run(self, nats_url: str, stop: asyncio.Event | None = None) -> None:
        """Connect to NATS, register pool, then enter the subscription loop.

        Overrides NatsAdapterBase.run() to inject pool.register() between
        nats_connect() and the blocking stop.wait(); uses run_embedded() for
        the main loop so all NatsAdapterBase bookkeeping is preserved.
        """
        nc = await nats_connect(
            nats_url,
            identity_name=self._identity_name,
            inbox_prefix=self._inbox_prefix,
        )
        self._nc = nc
        # Register pool (opens bridge sessions) BEFORE subscriptions.
        await self._pool.register(nc)

        if stop is None:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)

        try:
            await self.run_embedded(nc, stop)
        finally:
            # Cancel in-flight jobs then close the pool on shutdown.
            tasks = list(self._jobs)
            for t in tasks:
                if not t.done():
                    t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._jobs.clear()
            await self._pool.aclose()
            # Drain + close nc (our run override owns it; base _shutdown not called).
            # Suppress errors on shutdown path (best-effort, per other workers).
            if self._nc is not None:
                try:
                    await self._nc.drain()
                except Exception:  # noqa: BLE001
                    log.warning(
                        "omp_worker: nc.drain failed on shutdown", exc_info=True
                    )  # noqa: E501
                try:
                    await self._nc.close()
                except Exception:  # noqa: BLE001
                    log.warning(
                        "omp_worker: nc.close failed on shutdown", exc_info=True
                    )  # noqa: E501
                self._nc = None

    # ------------------------------------------------------------------
    # NatsAdapterBase overrides
    # ------------------------------------------------------------------

    def _extra_subjects(self) -> list[str]:
        return []

    async def handle(self, msg: Any, payload: dict) -> None:
        """Parse the job envelope and spawn a task for it (Model B)."""
        try:
            envelope = JobEnvelope.model_validate(payload)
        except (
            Exception
        ) as exc:  # pydantic.ValidationError — schema parse failure  # noqa: BLE001
            log.exception("omp_worker: failed to parse JobEnvelope")
            # ValidationError.__str__ may embed incoming values — use only
            # type name on the bus (ADR-073). Log the full exception locally above.
            job_id = payload.get("job_id", "unknown")
            await publish_job_error(self._nc, str(job_id), exc)
            return

        job_id = envelope.job_id
        prompt = envelope.payload.get("prompt", "")
        if not prompt:
            log.warning("omp_worker: job_id=%s has empty prompt — rejecting", job_id)
            await publish_job_error(self._nc, str(job_id), ValueError("empty prompt"))
            return

        # provider_session_id (session path) from envelope — basic guard.
        # Hub-minted + defense-in-depth (security review).
        provider_session_id: Any = envelope.payload.get("provider_session_id")
        if provider_session_id is not None:
            s = str(provider_session_id)
            if not (
                s.endswith(".jsonl")
                or "/sessions/" in s
                or s.startswith("/home/factory/.config/omp-pi")
            ):
                log.warning("omp_worker: job_id=%s bad session token — reject", job_id)
                await publish_job_error(
                    self._nc, str(job_id), ValueError("invalid session token")
                )
                return

        # pool_id is informational only in Model B — no longer a routing key;
        # each acquire() picks any available pool worker. Kept for the debug log.
        pool_id: str = envelope.payload.get("pool_id") or str(job_id)

        # provider_session_id: empty string → None (never pass empty string to pool).
        provider_session_id = envelope.payload.get("provider_session_id") or None

        model_cfg = envelope.payload.get("model_cfg", {})
        system_prompt = envelope.payload.get("system_prompt", "")
        requested_model: str | None = None
        if isinstance(model_cfg, dict):
            raw_model = model_cfg.get("model")
            if isinstance(raw_model, str) and raw_model.strip():
                requested_model = raw_model.strip()
        _cfg_keys = (
            sorted(model_cfg)
            if isinstance(model_cfg, dict)
            else type(model_cfg).__name__
        )
        _sp_len = len(system_prompt) if isinstance(system_prompt, str) else 0
        log.debug(
            "omp job %s: received model_cfg keys=%s model=%s"
            " system_prompt_len=%d (system_prompt V1: not applied)"
            " pool_id=%s provider_session_id=%s",
            job_id,
            _cfg_keys,
            requested_model,
            _sp_len,
            pool_id,
            provider_session_id,
        )

        task = asyncio.create_task(
            self._run_job(
                str(job_id),
                str(prompt),
                provider_session_id,
                model=requested_model,
            )
        )
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def _run_job(
        self,
        job_id: str,
        prompt: str,
        session_file: str | None,
        *,
        model: str | None = None,
    ) -> None:
        """Acquire a pool worker, run the job, release on completion."""
        log.info("omp_worker: job_id=%s start", job_id)
        start = time.monotonic()
        worker = None
        try:
            worker = await self._pool.acquire(session_file)
            await worker.bridge.run(
                prompt,
                job_id,
                session_file=worker.session_file,
                model=model,
            )
        except Exception as exc:  # pool.acquire / bridge.run  # noqa: BLE001
            # _run_job is create_task-spawned (non-blocking, frees core-NATS dispatch
            # for Model B). Runs *outside* _dispatch guard in adapter_base; must
            # self-handle + publish sanitized error (ADR-073: only type(exc).__name__
            # via publish_job_error/_classify; full log local only).
            # See module docstring, CLAUDE.md, axial review.
            log.exception("omp_worker: job_id=%s failed", job_id)
            await publish_job_error(
                self._nc, job_id, exc
            )  # type(exc).__name__ only, on the bus
        else:
            log.info(
                "omp_worker: job_id=%s done elapsed=%.1fs",
                job_id,
                time.monotonic() - start,
            )
        finally:
            if worker is not None:
                self._pool.release(worker)

    def heartbeat_payload(self) -> dict:
        base = super().heartbeat_payload()
        base["worker"] = "omp"
        return base
