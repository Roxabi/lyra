"""LlmCodec — pure encode/decode boundary between LlmClient and transport bytes.

No I/O, no network, no NATS imports. Replays NatsLlmClient._build_request for
encode; handles Result[bytes, SanitizedError] → LlmResult for decode.

CB is NOT touched on decode failure — see spec § "Error path — decode failure".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import ValidationError

from lyra.core.messaging.events import LlmEvent, ResultLlmEvent, TextLlmEvent
from lyra.core.ports.llm import LlmResult
from lyra.transport._result import Err, Ok, Result, SanitizedError
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.errors import KNOWN_CODES, WorkerError
from roxabi_contracts.llm import LlmChunkEvent, LlmRequest, LlmResponse

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import ModelConfig

log = logging.getLogger(__name__)


def _make_worker_error(
    code: str, message: str, retryable: bool, detail: str | None = None
) -> WorkerError:
    assert code in KNOWN_CODES, f"unknown WorkerError code: {code}"
    return WorkerError(code=code, message=message, retryable=retryable, detail=detail)


class LlmCodec:
    """Pure encode/decode for the LLM domain.

    encode: builds LlmRequest bytes + returns trace_id.
    decode: maps Result[bytes, SanitizedError] → LlmResult; never raises.
    """

    def encode(
        self,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        messages: list[dict] | None,
        *,
        stream: bool,
    ) -> tuple[bytes, str]:
        """Build canonical LlmRequest payload and return (bytes, trace_id).

        Text-folding (spec §"Expected Behavior" §3):
          messages None/empty → [{"role": "user", "content": text}]
          last msg content == text → pass through unchanged
          else → append {"role": "user", "content": text}
        """
        if not messages:
            wire_messages: list[dict] = [{"role": "user", "content": text}]
        elif messages[-1].get("content") == text:
            wire_messages = list(messages)
        else:
            wire_messages = list(messages) + [{"role": "user", "content": text}]

        trace_id = str(uuid4())
        request = LlmRequest(
            contract_version=CONTRACT_VERSION,
            trace_id=trace_id,
            issued_at=datetime.now(timezone.utc),
            request_id=str(uuid4()).replace("-", "")[:32],
            messages=wire_messages,
            model=model_cfg.model,
            system_prompt=system_prompt,
            stream=stream,
            max_tokens=getattr(model_cfg, "max_tokens", None),
            temperature=getattr(model_cfg, "temperature", None),
        )
        return request.model_dump_json(exclude_none=True).encode(), trace_id

    def decode(self, result: Result[bytes, SanitizedError], trace_id: str) -> LlmResult:
        """Map transport Result → LlmResult; CB NOT touched on decode failure."""
        if isinstance(result, Err):
            err = result.error
            return LlmResult(
                error=err.message,
                retryable=err.retryable,
                worker_error=_make_worker_error(
                    err.code, err.message, err.retryable, err.detail
                ),
            )
        assert isinstance(result, Ok)
        try:
            resp = LlmResponse.model_validate_json(result.value)
        except (ValidationError, ValueError) as exc:
            log.warning("LlmCodec.decode: validation error: %r", exc)
            return LlmResult(error="decode.validation_error", retryable=False)

        if not resp.ok:
            error_msg = resp.error or "LLM generation failed"
            if resp.worker_error is not None:
                return LlmResult(
                    error=error_msg,
                    retryable=resp.worker_error.retryable,
                    worker_error=resp.worker_error,
                )
            return LlmResult(
                error=error_msg,
                retryable=True,
                worker_error=_make_worker_error(
                    "worker.internal", error_msg, retryable=True
                ),
            )

        return LlmResult(
            result=resp.text or "", session_id=trace_id, error="", retryable=True
        )

    def decode_chunk(self, result: Result[bytes, SanitizedError]) -> LlmEvent | None:
        """Map a single transport chunk → LlmEvent. Returns None for no-op chunks.

        Returns ResultLlmEvent on error or done; TextLlmEvent on text delta;
        None if the chunk has neither error, done, nor delta (skip).
        """
        if isinstance(result, Err):
            err = result.error
            return ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                cost_usd=None,
                error_text=err.message,
                worker_error=_make_worker_error(
                    err.code, err.message, err.retryable, err.detail
                ),
            )
        assert isinstance(result, Ok)
        try:
            chunk = LlmChunkEvent.model_validate_json(result.value)
        except (ValidationError, ValueError) as exc:
            log.warning("LlmCodec.decode_chunk: validation error: %r", exc)
            return ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                cost_usd=None,
                error_text="decode.validation_error",
                worker_error=_make_worker_error(
                    "transport.parse", "validation_error", retryable=False
                ),
            )
        if chunk.is_error:
            return ResultLlmEvent(
                is_error=True,
                duration_ms=chunk.duration_ms or 0,
                cost_usd=None,
                error_text=chunk.error or "LLM stream error",
                worker_error=chunk.worker_error
                or _make_worker_error(
                    "worker.internal", chunk.error or "LLM stream error", retryable=True
                ),
            )
        if chunk.done:
            return ResultLlmEvent(
                is_error=False, duration_ms=chunk.duration_ms or 0, cost_usd=None
            )
        if chunk.delta:
            return TextLlmEvent(text=chunk.delta)
        return None
