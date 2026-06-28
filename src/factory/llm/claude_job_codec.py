"""ClaudeJobCodec — JobEnvelope / JobProgress / JobResult ↔ LlmResult / LlmEvent."""

from __future__ import annotations

import logging

from factory.core.messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
    ToolUseLlmEvent,
)
from factory.core.ports.llm import LlmResult
from roxabi_contracts.errors import KNOWN_CODES, WorkerError
from roxabi_contracts.jobs import JobProgress, JobResult

log = logging.getLogger(__name__)

_FALLBACK_CODE = "worker.internal"
_MALFORMED_ERROR = "claude worker returned a malformed result"
_TIMEOUT_ERROR = "claude request timed out"


def _validate_worker_error(we: WorkerError | None) -> WorkerError | None:
    if we is None or we.code in KNOWN_CODES:
        return we
    log.warning(
        "ClaudeJobCodec: unregistered WorkerError code %r — mapping to %r",
        we.code,
        _FALLBACK_CODE,
    )
    return WorkerError(
        code=_FALLBACK_CODE,
        message=we.message,
        retryable=we.retryable,
        detail=we.detail,
    )


class ClaudeJobCodec:
    """Decode clipool JobProgress/JobResult; helpers for hub-side error paths."""

    def decode_result(self, result: JobResult) -> LlmResult:
        if result.status == "success":
            data = result.data or {}
            return LlmResult(
                result=data.get("result", ""),
                session_id=data.get("session_id"),
            )

        validated = _validate_worker_error(result.error)
        error_msg = validated.message if validated else _MALFORMED_ERROR
        return LlmResult(
            error=error_msg,
            retryable=validated.retryable if validated else True,
            worker_error=validated,
        )

    def decode_progress(self, progress: JobProgress) -> LlmEvent | None:
        event_type = progress.event_type
        if event_type == "text":
            return TextLlmEvent(text=progress.partial_text or "")
        if event_type == "tool_use":
            return ToolUseLlmEvent(
                tool_name=progress.tool_name or "",
                tool_id=progress.tool_id or "",
                input=progress.tool_input or {},
            )
        return None

    def decode_terminal_progress(self, progress: JobProgress) -> LlmEvent | None:
        """Some workers may emit a terminal progress before result — ignore by default."""
        del progress
        return None

    def progress_to_terminal(self, result: JobResult) -> ResultLlmEvent:
        """Build a stream terminal event from JobResult."""
        if result.status == "error":
            validated = _validate_worker_error(result.error)
            return ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                cost_usd=None,
                error_text=validated.message if validated else _MALFORMED_ERROR,
                worker_error=validated,
            )
        data = result.data or {}
        return ResultLlmEvent(
            is_error=False,
            duration_ms=0,
            cost_usd=None,
            session_id=data.get("session_id"),
            worker_error=None,
        )

    def decode_malformed(self) -> LlmResult:
        worker_error = WorkerError(
            code="transport.parse",
            message=_MALFORMED_ERROR,
            retryable=False,
        )
        return LlmResult(
            error=_MALFORMED_ERROR,
            retryable=False,
            worker_error=worker_error,
        )

    def decode_timeout(self) -> LlmResult:
        worker_error = WorkerError(
            code="transport.timeout",
            message=_TIMEOUT_ERROR,
            retryable=True,
        )
        return LlmResult(
            error=_TIMEOUT_ERROR,
            retryable=True,
            worker_error=worker_error,
        )