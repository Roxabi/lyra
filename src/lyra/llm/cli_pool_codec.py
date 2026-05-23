"""CliPoolCodec — encode/decode boundary for the NATS CliPool path.

Implements LlmCodec Protocol for lyra.clipool.cmd / lyra.clipool.control.
Encodes CliCmdPayload; decodes CliChunkEvent into LlmResult / LlmEvent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from lyra.core.messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
    ToolUseLlmEvent,
)
from lyra.core.ports.llm import LlmResult
from lyra.core.trace import TraceContext
from lyra.transport._result import Err, Result, SanitizedError
from roxabi_contracts.cli.models import CliChunkEvent, CliCmdPayload, CliControlCmd
from roxabi_contracts.envelope import CONTRACT_VERSION

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import ModelConfig

log = logging.getLogger(__name__)


class _CliSessionStore(Protocol):
    async def set_cli_session(self, session_id: str, cli_session_id: str) -> None: ...
    async def get_cli_session(self, session_id: str) -> str | None: ...


class CliPoolCodec:
    """Codec for CliPool-over-NATS (lyra.clipool.cmd)."""

    _session_store: _CliSessionStore | None = None

    def set_session_store(self, store: _CliSessionStore) -> None:
        self._session_store = store

    def encode(
        self,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        messages: list[dict] | None,
        *,
        stream: bool,
        **kwargs: Any,
    ) -> tuple[bytes, str]:
        del messages  # CliCmdPayload carries text directly
        trace_id = str(uuid4())
        pool_id = kwargs.get("pool_id", "")
        lyra_session_id = kwargs.get("lyra_session_id") or pool_id
        agent_name = TraceContext.get_agent_name() or None

        _mcfg: dict = (
            model_cfg.model_dump() if hasattr(model_cfg, "model_dump") else model_cfg  # type: ignore[assignment]
        )

        payload = CliCmdPayload(
            contract_version=CONTRACT_VERSION,
            trace_id=trace_id,
            issued_at=datetime.now(timezone.utc),
            pool_id=pool_id,
            lyra_session_id=lyra_session_id,
            text=text,
            model_cfg=_mcfg,
            system_prompt=system_prompt,
            stream=stream,
            agent_name=agent_name,
            agent_email=None,
        )
        return payload.model_dump_json(exclude_none=True).encode(), trace_id

    def decode(self, result: Result[bytes, SanitizedError], trace_id: str) -> LlmResult:
        if isinstance(result, Err):
            err = result.error
            return LlmResult(error=err.message, retryable=err.retryable)
        try:
            chunk = CliChunkEvent.model_validate_json(result.value)
        except (ValidationError, ValueError) as exc:
            log.warning("CliPoolCodec.decode: validation error: %r", exc)
            return LlmResult(error="decode.validation_error", retryable=False)

        if chunk.is_error or chunk.event_type == "error":
            error_msg = (
                chunk.worker_error.message
                if chunk.worker_error
                else "LLM generation failed"
            )
            return LlmResult(
                error=error_msg,
                retryable=chunk.worker_error.retryable if chunk.worker_error else True,
            )
        return LlmResult(
            result=chunk.text or "",
            session_id=chunk.session_id or trace_id,
            error="",
            retryable=True,
        )

    def decode_chunk(self, result: Result[bytes, SanitizedError]) -> LlmEvent | None:
        if isinstance(result, Err):
            err = result.error
            return ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                cost_usd=None,
                error_text=err.message,
            )
        try:
            chunk = CliChunkEvent.model_validate_json(result.value)
        except (ValidationError, ValueError) as exc:
            log.warning("CliPoolCodec.decode_chunk: validation error: %r", exc)
            return ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                cost_usd=None,
                error_text="decode.validation_error",
            )

        if chunk.is_error or chunk.event_type == "error":
            sanitized = (
                chunk.worker_error.message
                if chunk.worker_error
                else "LLM stream error"
            )
            return ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                cost_usd=None,
                error_text=sanitized,
                worker_error=chunk.worker_error,
            )

        if chunk.event_type == "text":
            return TextLlmEvent(text=chunk.text or "")

        if chunk.event_type == "tool_use":
            return ToolUseLlmEvent(
                tool_name=chunk.tool_name or "",
                tool_id=chunk.tool_id or "",
                input=chunk.tool_input or {},
            )

        if chunk.event_type == "result" or chunk.done:
            return ResultLlmEvent(
                is_error=chunk.is_error,
                duration_ms=0,
                cost_usd=None,
                session_id=chunk.session_id,
                worker_error=chunk.worker_error,
            )

        if chunk.event_type == "session_id":
            return None

        return None

    def encode_control(self, cmd: CliControlCmd) -> bytes:
        return cmd.model_dump_json(exclude_none=True).encode()
