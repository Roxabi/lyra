"""OmpWorker — NATS worker adapter that dispatches jobs to omp_rpc.

Subscribes to factory.jobs.omp (queue group omp-workers), translates
job envelopes into omp_rpc.RpcClient.prompt_and_wait() calls, and
publishes JobProgress / JobResult events back onto the bus.

Thread model: omp_rpc invokes listener callbacks on its own
omp-rpc-stdout daemon thread (no running event loop there); the bridge
marshals each NATS publish back onto the worker's event loop via
loop.call_soon_threadsafe (see RpcBridge._schedule_publish).

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

from factory.adapters.omp._rpc_bridge import RpcBridge
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
    """NATS worker that dispatches omp jobs via RpcBridge.

    One RpcBridge per worker process (one omp_rpc session per
    process lifetime).  Concurrent jobs are serialised by the
    omp_rpc subprocess model — a second job arriving while
    prompt_and_wait is in flight will be processed after the
    current one completes (queue-group distribution ensures only
    one message is in flight per worker replica at a time).
    """

    def __init__(
        self,
        bridge: RpcBridge | None = None,
        *,
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
        # Allow injection for testing; production always constructs real bridge.
        self._bridge: RpcBridge = bridge if bridge is not None else RpcBridge()

    # ------------------------------------------------------------------
    # Lifecycle override — connect then register bridge before entering
    # the main subscription loop.
    # ------------------------------------------------------------------

    async def run(self, nats_url: str, stop: asyncio.Event | None = None) -> None:
        """Connect to NATS, register bridge, then enter the subscription loop.

        Overrides NatsAdapterBase.run() to inject bridge.register() between
        nats_connect() and the blocking stop.wait(); uses run_embedded() for
        the main loop so all NatsAdapterBase bookkeeping is preserved.
        """
        nc = await nats_connect(
            nats_url,
            identity_name=self._identity_name,
            inbox_prefix=self._inbox_prefix,
        )
        # Register omp_rpc callbacks and open session BEFORE subscriptions.
        await self._bridge.register(nc)

        if stop is None:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)

        try:
            await self.run_embedded(nc, stop)
        finally:
            # Stop the omp_rpc subprocess on shutdown (idempotent).
            await self._bridge.aclose()

    # ------------------------------------------------------------------
    # NatsAdapterBase overrides
    # ------------------------------------------------------------------

    def _extra_subjects(self) -> list[str]:
        return []

    async def handle(self, msg: Any, payload: dict) -> None:
        """Dispatch a received job envelope to the RpcBridge."""
        try:
            envelope = JobEnvelope.model_validate(payload)
        except (
            Exception
        ) as exc:  # pydantic.ValidationError — schema parse failure  # noqa: BLE001
            log.exception("omp_worker: failed to parse JobEnvelope")
            # ValidationError.__str__ may embed incoming values — use only
            # type name on the bus (ADR-073). Log the full exception locally above.
            job_id = payload.get("job_id", "unknown")
            await self._bridge.publish_error(str(job_id), exc)
            return

        job_id = envelope.job_id
        prompt = envelope.payload.get("prompt", "")
        if not prompt:
            log.warning("omp_worker: job_id=%s has empty prompt — rejecting", job_id)
            await self._bridge.publish_error(str(job_id), ValueError("empty prompt"))
            return
        model_cfg = envelope.payload.get("model_cfg", {})
        system_prompt = envelope.payload.get("system_prompt", "")
        _cfg_keys = (
            sorted(model_cfg)
            if isinstance(model_cfg, dict)
            else type(model_cfg).__name__
        )
        _sp_len = len(system_prompt) if isinstance(system_prompt, str) else 0
        log.debug(
            "omp job %s: received model_cfg keys=%s"
            " system_prompt_len=%d (V1: not applied)",
            job_id,
            _cfg_keys,
            _sp_len,
        )
        log.info("omp_worker: job_id=%s start", job_id)
        start = time.monotonic()
        try:
            await self._bridge.run(prompt=str(prompt), job_id=str(job_id))
        except (
            Exception
        ) as exc:  # propagated from prompt_and_wait; publish error  # noqa: BLE001
            log.exception("omp_worker: job_id=%s failed", job_id)
            await self._bridge.publish_error(str(job_id), exc)
        else:
            elapsed = time.monotonic() - start
            log.info("omp_worker: job_id=%s done elapsed=%.1fs", job_id, elapsed)

    def heartbeat_payload(self) -> dict:
        base = super().heartbeat_payload()
        base["worker"] = "omp"
        return base
