"""CliPoolNatsWorker — NATS worker adapter for CliPool.

(ADR-054 (absorbed into ADR-055)).

Subscribes to ``factory.clipool.cmd`` (queue group ``clipool-workers``) and
``factory.clipool.control``.  Routes inbound messages to _handle_cmd or
_handle_control based on subject.  Streams CLI output back to the caller
via NATS request-reply inbox.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from factory.adapters.clipool._control_dispatch import dispatch_control
from factory.adapters.clipool._pool_bridge import run_pool_op
from factory.adapters.clipool._streaming_relay import relay_streaming_events
from factory.adapters.clipool._worker_helpers import _make_ack, _make_chunk
from factory.adapters.clipool.error_classifier import worker_error_from_cli_result
from factory.core.agent.agent_config import ModelConfig
from factory.core.cli.cli_pool import CliPool, CliResult
from factory.core.messaging.utils.metrics import emit_populated_total
from roxabi_contracts.cli.models import CliCmdPayload, CliControlCmd
from roxabi_contracts.errors import WorkerError
from roxabi_nats.adapter_base import NatsAdapterBase

log = logging.getLogger(__name__)

_CMD_SUBJECT = "factory.clipool.cmd"
_CONTROL_SUBJECT = "factory.clipool.control"
_HEARTBEAT_SUBJECT = "factory.clipool.heartbeat"
_QUEUE_GROUP = "clipool-workers"
_ENVELOPE_NAME = "CliCmdPayload"
_SCHEMA_VERSION = 1
_HEARTBEAT_INTERVAL = 30.0


class CliPoolNatsWorker(NatsAdapterBase):
    """NATS worker adapter that exposes CliPool over request-reply subjects.

    Routing:
      - ``factory.clipool.cmd``     (queue group) → _handle_cmd
      - ``factory.clipool.control`` (broadcast)   → _handle_control

    The caller sends a JSON envelope (CliCmdPayload / CliControlCmd) and
    provides a reply-to inbox.  Streaming chunks are published to that inbox
    as CliChunkEvent messages.  A terminal chunk with ``done=True`` signals
    end-of-turn.
    """

    def __init__(
        self,
        pool: CliPool,
        *,
        timeout: float = 30.0,
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
            wait_ready=False,  # worker semantics — see NatsAdapterBase docstring
        )
        self._pool = pool

    # ------------------------------------------------------------------
    # NatsAdapterBase overrides
    # ------------------------------------------------------------------

    def _extra_subjects(self) -> list[str]:
        return [_CONTROL_SUBJECT]

    async def handle(self, msg: Any, payload: dict) -> None:
        if msg.subject == _CONTROL_SUBJECT:
            await self._handle_control(msg, payload)
        else:
            await self._handle_cmd(msg, payload)

    def heartbeat_payload(self) -> dict:
        base = super().heartbeat_payload()
        base["pool_count"] = len(self._pool._entries)
        return base

    # ------------------------------------------------------------------
    # Command handler (streaming + non-streaming)
    # ------------------------------------------------------------------

    async def _handle_cmd(self, msg: Any, payload: dict) -> None:
        try:
            cmd = CliCmdPayload.model_validate(payload)
        except ValidationError:
            log.exception("clipool_worker: failed to parse CliCmdPayload")
            # The JSON decoded successfully (NatsAdapterBase did that before
            # dispatching to handle()) but the payload failed schema validation
            # — this is the textbook `worker.validation` case (decoded OK but
            # fields are wrong, e.g. caller on an old contract version).
            # `transport.parse` would mean "couldn't decode bytes/JSON", which
            # is a different failure mode handled one layer up.
            # Sanitize bus-bound message (#1215). ValidationError __str__
            # embeds incoming field values from CliCmdPayload — full %r in
            # log.exception above only.
            worker_error = WorkerError(
                code="worker.validation",
                message="CliCmdPayload validation failed",
                retryable=False,
            )
            emit_populated_total(domain="cli")
            await self.reply(
                msg,
                _make_chunk(
                    "",
                    event_type="error",
                    is_error=True,
                    done=True,
                    worker_error=worker_error,
                ),
            )
            return

        model_cfg = ModelConfig.model_validate(cmd.model_cfg)

        resumed: bool | None = None
        if cmd.resume_session_id:
            resumed = await self._pool.resume_direct(cmd.pool_id, cmd.resume_session_id)
            log.info(
                "clipool_worker: resume %s pool=%s",
                "queued" if resumed else "cold-start",
                cmd.pool_id,
            )

        if cmd.stream:
            await self._handle_cmd_streaming(msg, cmd, model_cfg, resumed=resumed)
        else:
            await self._handle_cmd_blocking(msg, cmd, model_cfg, resumed=resumed)

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

    async def _handle_cmd_streaming(
        self,
        msg: Any,
        cmd: CliCmdPayload,
        model_cfg: ModelConfig,
        *,
        resumed: bool | None = None,
    ) -> None:
        iterator = await self._run_pool_op(
            msg,
            cmd.pool_id,
            self._pool.send_streaming(
                cmd.pool_id,
                cmd.text,
                model_cfg,
                cmd.system_prompt,
                agent_name=cmd.agent_name,
                agent_email=cmd.agent_email,
                lyra_session_id=cmd.lyra_session_id,
            ),
            direct_publish=True,
        )
        if iterator is None or isinstance(iterator, bytes):
            return

        await relay_streaming_events(
            iterator=iterator,
            pool_id=cmd.pool_id,
            resumed=resumed,
            msg=msg,
            reply=self.reply,
        )

    async def _handle_cmd_blocking(
        self,
        msg: Any,
        cmd: CliCmdPayload,
        model_cfg: ModelConfig,
        *,
        resumed: bool | None = None,
    ) -> None:
        result = await self._run_pool_op(
            msg,
            cmd.pool_id,
            self._pool.send(
                cmd.pool_id,
                cmd.text,
                model_cfg,
                cmd.system_prompt,
                agent_name=cmd.agent_name,
                agent_email=cmd.agent_email,
                lyra_session_id=cmd.lyra_session_id,
            ),
            direct_publish=False,
        )
        if result is None or isinstance(result, bytes) or not isinstance(result, CliResult):
            return

        worker_error = (
            worker_error_from_cli_result(result.error) if result.error else None
        )
        if worker_error is not None:
            emit_populated_total(domain="cli")
        chunk = _make_chunk(
            cmd.pool_id,
            event_type="result",
            is_error=bool(result.error),
            text=result.result or None,
            session_id=result.session_id or None,
            done=True,
            resumed=resumed,
            worker_error=worker_error,
        )
        await self.reply(msg, chunk)

    # ------------------------------------------------------------------
    # Control handler (reset / resume_and_reset / switch_cwd)
    # ------------------------------------------------------------------

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

    async def _dispatch_control(self, cmd: CliControlCmd) -> bytes:
        """Delegate to :func:`dispatch_control` (kept for direct unit tests)."""
        return await dispatch_control(self._pool, cmd)