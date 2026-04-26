"""CliPoolNatsWorker — NATS worker adapter for CliPool (ADR-054).

Subscribes to ``lyra.clipool.cmd`` (queue group ``clipool-workers``) and
``lyra.clipool.control``.  Routes inbound messages to _handle_cmd or
_handle_control based on subject.  Streams CLI output back to the caller
via NATS request-reply inbox.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.cli.cli_pool import CliPool
from lyra.core.messaging.events import ResultLlmEvent, TextLlmEvent
from roxabi_contracts.cli.models import (
    CliChunkEvent,
    CliCmdPayload,
    CliControlAck,
    CliControlCmd,
)
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_nats.adapter_base import NatsAdapterBase

log = logging.getLogger(__name__)

_CMD_SUBJECT = "lyra.clipool.cmd"
_CONTROL_SUBJECT = "lyra.clipool.control"
_HEARTBEAT_SUBJECT = "lyra.clipool.heartbeat"
_QUEUE_GROUP = "clipool-workers"
_ENVELOPE_NAME = "CliCmdPayload"
_SCHEMA_VERSION = 1
_HEARTBEAT_INTERVAL = 30.0


def _make_chunk(pool_id: str, **kwargs: Any) -> bytes:
    """Serialise a CliChunkEvent to JSON bytes for NATS publish."""
    import uuid
    from datetime import datetime, timezone

    event = CliChunkEvent(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid.uuid4()),
        issued_at=datetime.now(timezone.utc),
        pool_id=pool_id,
        **kwargs,
    )
    return event.model_dump_json().encode()


def _make_ack(pool_id: str, **kwargs: Any) -> bytes:
    """Serialise a CliControlAck to JSON bytes for NATS publish."""
    import uuid
    from datetime import datetime, timezone

    ack = CliControlAck(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid.uuid4()),
        issued_at=datetime.now(timezone.utc),
        pool_id=pool_id,
        **kwargs,
    )
    return ack.model_dump_json().encode()


class CliPoolNatsWorker(NatsAdapterBase):
    """NATS worker adapter that exposes CliPool over request-reply subjects.

    Routing:
      - ``lyra.clipool.cmd``     (queue group) → _handle_cmd
      - ``lyra.clipool.control`` (broadcast)   → _handle_control

    The caller sends a JSON envelope (CliCmdPayload / CliControlCmd) and
    provides a reply-to inbox.  Streaming chunks are published to that inbox
    as CliChunkEvent messages.  A terminal chunk with ``done=True`` signals
    end-of-turn.
    """

    def __init__(self, pool: CliPool, *, timeout: float = 30.0) -> None:
        super().__init__(
            subject=_CMD_SUBJECT,
            queue_group=_QUEUE_GROUP,
            envelope_name=_ENVELOPE_NAME,
            schema_version=_SCHEMA_VERSION,
            timeout=timeout,
            heartbeat_subject=_HEARTBEAT_SUBJECT,
            heartbeat_interval=_HEARTBEAT_INTERVAL,
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
        except Exception:
            log.exception("clipool_worker: failed to parse CliCmdPayload")
            await self.reply(
                msg, _make_chunk("", event_type="error", is_error=True, done=True)
            )
            return

        model_cfg = ModelConfig.model_validate(cmd.model_cfg)

        if cmd.stream:
            await self._handle_cmd_streaming(msg, cmd, model_cfg)
        else:
            await self._handle_cmd_blocking(msg, cmd, model_cfg)

    async def _handle_cmd_streaming(
        self, msg: Any, cmd: CliCmdPayload, model_cfg: ModelConfig
    ) -> None:
        try:
            iterator = await self._pool.send_streaming(
                cmd.pool_id,
                cmd.text,
                model_cfg,
                cmd.system_prompt,
            )
        except Exception:
            log.exception(
                "clipool_worker: send_streaming failed for pool_id=%r", cmd.pool_id
            )
            if msg.reply and self._nc:
                await self._nc.publish(
                    msg.reply,
                    _make_chunk(
                        cmd.pool_id,
                        event_type="error",
                        is_error=True,
                        done=True,
                    ),
                )
            return

        async for event in iterator:
            if isinstance(event, TextLlmEvent):
                chunk = _make_chunk(
                    cmd.pool_id,
                    event_type="text",
                    text=event.text,
                    done=False,
                )
                await self.reply(msg, chunk)
            elif isinstance(event, ResultLlmEvent):
                chunk = _make_chunk(
                    cmd.pool_id,
                    event_type="result",
                    is_error=event.is_error,
                    done=True,
                )
                await self.reply(msg, chunk)
                return
            # ToolUseLlmEvent — skip; tool use is internal to claude CLI
        # Iterator exhausted without a ResultLlmEvent — send synthetic terminal chunk.
        await self.reply(
            msg,
            _make_chunk(cmd.pool_id, event_type="result", done=True),
        )

    async def _handle_cmd_blocking(
        self, msg: Any, cmd: CliCmdPayload, model_cfg: ModelConfig
    ) -> None:
        try:
            result = await self._pool.send(
                cmd.pool_id,
                cmd.text,
                model_cfg,
                cmd.system_prompt,
            )
        except Exception:
            log.exception("clipool_worker: send failed for pool_id=%r", cmd.pool_id)
            await self.reply(
                msg,
                _make_chunk(
                    cmd.pool_id,
                    event_type="error",
                    is_error=True,
                    done=True,
                ),
            )
            return

        chunk = _make_chunk(
            cmd.pool_id,
            event_type="result",
            is_error=bool(result.error),
            session_id=result.session_id or None,
            done=True,
        )
        await self.reply(msg, chunk)

    # ------------------------------------------------------------------
    # Control handler (reset / resume_and_reset / switch_cwd)
    # ------------------------------------------------------------------

    async def _handle_control(self, msg: Any, payload: dict) -> None:
        try:
            cmd = CliControlCmd.model_validate(payload)
        except Exception:
            log.exception("clipool_worker: failed to parse CliControlCmd")
            await self.reply(msg, _make_ack("", ok=False))
            return

        try:
            ack_bytes = await self._dispatch_control(cmd)
        except Exception:
            log.exception(
                "clipool_worker: control op %r failed for pool_id=%r",
                cmd.op,
                cmd.pool_id,
            )
            ack_bytes = _make_ack(cmd.pool_id, ok=False)

        await self.reply(msg, ack_bytes)

    async def _dispatch_control(self, cmd: CliControlCmd) -> bytes:
        if cmd.op == "reset":
            await self._pool.reset(cmd.pool_id)
            return _make_ack(cmd.pool_id, ok=True)

        if cmd.op == "resume_and_reset":
            if not cmd.session_id:
                log.warning(
                    "clipool_worker: resume_and_reset missing session_id"
                    " for pool_id=%r",
                    cmd.pool_id,
                )
                return _make_ack(cmd.pool_id, ok=False)
            resumed = await self._pool.resume_and_reset(cmd.pool_id, cmd.session_id)
            return _make_ack(cmd.pool_id, ok=True, resumed=resumed)

        if cmd.op == "switch_cwd":
            if not cmd.cwd:
                log.warning(
                    "clipool_worker: switch_cwd missing cwd for pool_id=%r",
                    cmd.pool_id,
                )
                return _make_ack(cmd.pool_id, ok=False)
            base_dir = Path(
                os.environ.get("LYRA_CLAUDE_CWD", str(Path.home() / "projects"))
            ).resolve()
            try:
                resolved = Path(cmd.cwd).resolve()
                resolved.relative_to(base_dir)  # raises ValueError if outside
            except ValueError:
                log.warning(
                    "clipool_worker: switch_cwd path %r escapes base %r for pool_id=%r",
                    cmd.cwd,
                    str(base_dir),
                    cmd.pool_id,
                )
                return _make_ack(cmd.pool_id, ok=False)
            await self._pool.switch_cwd(cmd.pool_id, resolved)
            return _make_ack(cmd.pool_id, ok=True)

        log.warning(
            "clipool_worker: unknown control op %r for pool_id=%r",
            cmd.op,
            cmd.pool_id,
        )
        return _make_ack(cmd.pool_id, ok=False)
