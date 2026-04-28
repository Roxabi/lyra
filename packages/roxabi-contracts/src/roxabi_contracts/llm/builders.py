"""Response builders for LLM-domain NATS contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.llm.models import LlmChunkEvent, LlmResponse

__all__ = ["build_llm_chunk", "build_llm_response"]


def build_llm_response(  # noqa: PLR0913 — builder with optional success/error fields
    payload: dict[str, Any],
    *,
    ok: bool,
    text: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
    issued_at: datetime | None = None,
) -> str:
    """Build an LlmResponse JSON string from a request payload.

    Args:
        payload: The request payload dict. Must contain 'request_id'.
            'trace_id' is optional; falls back to request_id.
        ok: Success flag. When True, text is required (validated by model).
        text: Generated text (required when ok=True).
        error: Error message (required when ok=False).
        duration_ms: Total generation time in milliseconds.
        issued_at: Timestamp to embed. Defaults to ``datetime.now(timezone.utc)``.
    """
    request_id = payload["request_id"]
    trace_id = payload.get("trace_id") or request_id
    response = LlmResponse(
        contract_version=CONTRACT_VERSION,
        trace_id=trace_id,
        issued_at=issued_at if issued_at is not None else datetime.now(timezone.utc),
        ok=ok,
        request_id=request_id,
        text=text,
        error=error,
        duration_ms=duration_ms,
    )
    return response.model_dump_json()


def build_llm_chunk(  # noqa: PLR0913 — builder with optional chunk fields
    payload: dict[str, Any],
    *,
    delta: str | None = None,
    done: bool = False,
    is_error: bool = False,
    error: str | None = None,
    duration_ms: int | None = None,
    issued_at: datetime | None = None,
) -> str:
    """Build an LlmChunkEvent JSON string from a request payload."""
    request_id = payload["request_id"]
    trace_id = payload.get("trace_id") or request_id
    chunk = LlmChunkEvent(
        contract_version=CONTRACT_VERSION,
        trace_id=trace_id,
        issued_at=issued_at if issued_at is not None else datetime.now(timezone.utc),
        request_id=request_id,
        delta=delta,
        done=done,
        is_error=is_error,
        error=error,
        duration_ms=duration_ms,
    )
    return chunk.model_dump_json()
