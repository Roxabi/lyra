"""OMP PromptTurn → WorkerError mapping for RpcBridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from roxabi_contracts.errors import WorkerError


@dataclass(frozen=True)
class TurnPublishContext:
    turn: Any
    turn_error: WorkerError | None
    model_fallback: dict[str, str] | None
    session_file: str | None


def _field(obj: Any, *names: str) -> Any:
    """Read the first present attribute/key from a TypedDict or dataclass."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return None
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _assistant_message_from_turn(turn: Any, end_event: Any | None) -> Any | None:
    """Best-effort assistant message from PromptTurn or stored AgentEndEvent."""
    assistant = getattr(turn, "assistant_message", None)
    if assistant is not None:
        return assistant
    if end_event is None:
        return None
    messages = getattr(end_event, "messages", None)
    if not messages:
        return None
    for candidate in reversed(messages):
        role = _field(candidate, "role")
        if role == "assistant":
            return candidate
    return None


def worker_error_from_omp_turn(turn: Any, end_event: Any | None) -> WorkerError | None:
    """Map an OMP PromptTurn provider failure to a structured WorkerError.

    ADR-073: returned ``WorkerError.message`` is a stable type label only —
    never the provider's ``errorMessage`` string.
    """
    assistant = _assistant_message_from_turn(turn, end_event)
    stop_reason = _field(assistant, "stopReason", "stop_reason")
    if stop_reason == "aborted":
        return WorkerError(code="worker.internal", message="Aborted", retryable=True)
    if stop_reason != "error":
        return None

    error_message = _field(assistant, "errorMessage", "error_message")
    if error_message:
        lower = str(error_message).lower()
        if "invalid model" in lower:
            return WorkerError(
                code="llm.model_unavailable",
                message="ModelUnavailable",
                retryable=False,
            )
        if "rate limit" in lower or "429" in lower:
            return WorkerError(
                code="llm.rate_limit",
                message="RateLimitError",
                retryable=True,
            )
        if "context" in lower and (
            "too long" in lower or "length" in lower or "window" in lower
        ):
            return WorkerError(
                code="llm.context_too_long",
                message="ContextTooLong",
                retryable=False,
            )
    return WorkerError(
        code="worker.internal",
        message="OmpProviderError",
        retryable=False,
    )