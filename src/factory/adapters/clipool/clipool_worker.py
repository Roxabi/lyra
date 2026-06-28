"""CliPoolNatsWorker — NATS worker adapter for CliPool (phase 2 JobEnvelope).

Subscribes to ``factory.jobs.claude`` (queue group) and ``factory.clipool.control``.
Dispatches clipool runs from JobEnvelope payloads and publishes
``factory.job.<id>.{progress,result}`` — same calling model as OmpWorker.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from factory.adapters.clipool._control_dispatch import dispatch_control
from factory.adapters.clipool._pool_bridge import run_pool_op
from factory.adapters.clipool._streaming_publish import (
    publish_blocking_result,
    publish_job_failure,
    publish_streaming_job,
)
from factory.adapters.clipool._worker_helpers import _make_ack
from factory.adapters.omp._rpc_envelope import publish_job_error
from factory.core.agent.agent_config import ModelConfig
from factory.core.cli.cli_pool import CliPool, CliResult
from factory.core.messaging.utils.metrics import emit_populated_total
from roxabi_contracts.cli import SUBJECTS
from roxabi_contracts.cli.models import CliControlCmd
from roxabi_contracts.jobs.models import JobEnvelope
from roxabi_contracts.jobs.subjects import jobs_runtime_claude
from roxabi_nats.adapter_base import NatsAdapterBase

log = logging.getLogger(__name__)

_ENVELOPE_NAME = "JobEnvelope"
_SCHEMA_VERSION = 1
_HEARTBEAT_INTERVAL = 30.0


class CliPoolNatsWorker(NatsAdapterBase):
    """NATS worker exposing CliPool via JobEnvelope dispatch + pub/sub facets."""

    def __init__(
        self,
        pool: CliPool,
        *,
        timeout: float = 30.0,
        identity_name: str | None = None,
    ) -> None:
        super().__init__(
            subject=jobs_runtime_claude(),
            queue_group=SUBJECTS.clipool_workers,
            envelope_name=_ENVELOPE_NAME,
            schema_version=_SCHEMA_VERSION,
            timeout=timeout,
            heartbeat_subject=SUBJECTS.heartbeat,
            heartbeat_interval=_HEARTBEAT_INTERVAL,
            identity_name=identity_name,
            wait_ready=False,
        )
        self._pool = pool
        self._jobs: set[asyncio.Task] = set()

    def _extra_subjects(self) -> list[str]:
        return [SUBJECTS.control]

    async def handle(self, msg: Any, payload: dict) -> None:
        if msg.subject == SUBJECTS.control:
            await self._handle_control(msg, payload)
            return

        try:
            envelope = JobEnvelope.model_validate(payload)
        except ValidationError:
            log.exception("clipool_worker: failed to parse JobEnvelope")
            job_id = str(payload.get("job_id", "unknown"))
            from factory.adapters.omp._rpc_envelope import make_result
            from roxabi_contracts.errors import WorkerError
            from roxabi_contracts.jobs.subjects import jobs_result

            worker_error = WorkerError(
                code="worker.validation",
                message="ValidationError",
                retryable=False,
            )
            await self._nc.publish(
                jobs_result(job_id),
                make_result(job_id, status="error", error=worker_error),
            )
            return

        task = asyncio.create_task(self._run_job(envelope))
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    def heartbeat_payload(self) -> dict:
        base = super().heartbeat_payload()
        base["pool_count"] = len(self._pool._entries)
        return base

    async def _run_job(self, envelope: JobEnvelope) -> None:
        job_id = envelope.job_id
        body = envelope.payload
        pool_id = str(body.get("pool_id") or job_id)
        prompt = str(body.get("prompt") or body.get("text") or "")
        if not prompt:
            log.warning("clipool_worker: job_id=%s empty prompt — rejecting", job_id)
            await publish_job_error(self._nc, job_id, ValueError("empty prompt"))
            return

        stream = bool(body.get("stream", True))
        model_cfg = ModelConfig.model_validate(body.get("model_cfg") or {})
        system_prompt = str(body.get("system_prompt") or "")
        agent_name = body.get("agent_name")
        agent_email = body.get("agent_email")
        lyra_session_id = str(body.get("lyra_session_id") or pool_id)
        provider_session_id = body.get("provider_session_id") or body.get(
            "resume_session_id"
        )

        resumed: bool | None = None
        if provider_session_id:
            resumed = await self._pool.resume_direct(pool_id, str(provider_session_id))
            log.info(
                "clipool_worker: resume %s pool=%s job=%s",
                "queued" if resumed else "cold-start",
                pool_id,
                job_id,
            )

        try:
            if stream:
                iterator = await self._pool.send_streaming(
                    pool_id,
                    prompt,
                    model_cfg,
                    system_prompt,
                    agent_name=agent_name,
                    agent_email=agent_email,
                    lyra_session_id=lyra_session_id,
                )
                await publish_streaming_job(
                    self._nc,
                    job_id=job_id,
                    iterator=iterator,
                    resumed=resumed,
                )
            else:
                result = await self._pool.send(
                    pool_id,
                    prompt,
                    model_cfg,
                    system_prompt,
                    agent_name=agent_name,
                    agent_email=agent_email,
                    lyra_session_id=lyra_session_id,
                )
                if not isinstance(result, CliResult):
                    await publish_job_error(
                        self._nc, job_id, RuntimeError("invalid pool result")
                    )
                    return
                if result.error:
                    emit_populated_total(domain="cli")
                await publish_blocking_result(
                    self._nc,
                    job_id=job_id,
                    result=result,
                    resumed=resumed,
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("clipool_worker: job_id=%s failed", job_id)
            emit_populated_total(domain="cli")
            await publish_job_failure(self._nc, job_id=job_id, exc=exc)

    async def _run_pool_op(
        self,
        msg: Any,
        pool_id: str,
        coro,
        *,
        direct_publish: bool,
        control_ack: bool = False,
    ):
        return await run_pool_op(
            coro=coro,
            pool_id=pool_id,
            msg=msg,
            reply=self.reply,
            nc=self._nc,
            direct_publish=direct_publish,
            control_ack=control_ack,
        )

    async def _handle_control(self, msg: Any, payload: dict) -> None:
        try:
            cmd = CliControlCmd.model_validate(payload)
        except ValidationError:
            log.exception("clipool_worker: failed to parse CliControlCmd")
            await self.reply(msg, _make_ack("", ok=False))
            return

        ack_bytes = await self._run_pool_op(
            msg,
            cmd.pool_id,
            dispatch_control(self._pool, cmd),
            direct_publish=False,
            control_ack=True,
        )
        await self.reply(msg, ack_bytes or _make_ack(cmd.pool_id, ok=False))