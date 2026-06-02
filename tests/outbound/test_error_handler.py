"""Unit tests for OutboundErrorHandler (T8b, #1279).

Validates sanitization discipline (type name only — never str(exc)),
guard() Result wrapping, classify_stream_error() routing, and that
guard() logs at DEBUG (not WARNING/ERROR) to avoid leaking context
via log channels.
"""

from __future__ import annotations

import dataclasses
import logging

import httpx
import pytest

from factory.outbound.error_handler import OutboundErrorHandler
from factory.transport._result import (
    Err,
    Ok,
    SanitizedError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _passthrough_get_msg(key: str, fallback: str) -> str:
    """Return the fallback unchanged — used when we want to inspect raw values."""
    return fallback


def _make_handler() -> OutboundErrorHandler:
    return OutboundErrorHandler(get_msg=_passthrough_get_msg)


# ---------------------------------------------------------------------------
# handle() — sanitization
# ---------------------------------------------------------------------------


def test_handle_uses_type_name_only() -> None:
    """handle() returns type name only; connection details must not appear."""
    handler = _make_handler()
    exc = httpx.ConnectError("server=10.0.0.5:4222 token=ABC")

    result: SanitizedError = handler.handle(exc, context="test_ctx")

    assert isinstance(result, SanitizedError)
    assert result.code == "test_ctx"
    assert result.message == "ConnectError"
    assert result.retryable is False

    # Dump all fields and verify no secret payload surfaces anywhere.
    all_fields = dataclasses.asdict(result)
    serialized = str(all_fields)
    assert "10.0.0.5" not in serialized
    assert "token=ABC" not in serialized


# ---------------------------------------------------------------------------
# guard() — Result wrapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_returns_ok_on_success() -> None:
    """guard() wraps a successful awaitable in Ok."""
    handler = _make_handler()

    async def noop() -> int:
        return 42

    result = await handler.guard(noop, context="ok_path")

    assert isinstance(result, Ok)
    assert result.value == 42


@pytest.mark.asyncio
async def test_guard_returns_err_on_exception() -> None:
    """guard() wraps an exception in Err; message is type name only."""
    handler = _make_handler()

    async def boom() -> None:
        raise httpx.ConnectError("server=internal-host:4222")

    result = await handler.guard(boom, context="boom_ctx")

    assert isinstance(result, Err)
    assert result.error.message == "ConnectError"
    assert result.error.code == "boom_ctx"

    # No host fragment must escape into any error field.
    all_fields = dataclasses.asdict(result.error)
    serialized = str(all_fields)
    assert "internal-host" not in serialized


# ---------------------------------------------------------------------------
# classify_stream_error() — routing
# ---------------------------------------------------------------------------


def test_classify_stream_error_timeout() -> None:
    """StreamChunkTimeout is rendered via the 'error_timeout' key / fallback."""
    from factory.core.exceptions import StreamChunkTimeout

    handler = _make_handler()
    exc = StreamChunkTimeout(120.0)

    text = handler.classify_stream_error(exc, had_tool_events=False, final_text=None)

    assert text is not None
    # The fallback string contains "120" AND "timeout" — either is sufficient.
    assert "120" in text or "timeout" in text.lower()


def test_classify_stream_error_non_timeout_uses_type_name_only() -> None:
    """Non-timeout exceptions are rendered as type name only — no payload leaking."""

    class CustomTransportError(Exception):
        pass

    handler = _make_handler()
    exc = CustomTransportError("server=internal:4222 token=AKIA...")

    text = handler.classify_stream_error(exc, had_tool_events=False, final_text=None)

    assert text is not None
    assert "CustomTransportError" in text
    assert "internal" not in text
    assert "token=" not in text
    assert "AKIA" not in text


def test_classify_stream_error_no_error_no_tool_events_returns_none() -> None:
    """No error + final text present → None (caller renders final_text normally)."""
    handler = _make_handler()

    result = handler.classify_stream_error(None, had_tool_events=False, final_text="ok")

    assert result is None


def test_classify_stream_error_no_error_no_tool_events_no_final_returns_none() -> None:
    """No error + no tool events + no final → None (caller uses GENERIC_ERROR_REPLY)."""
    handler = _make_handler()

    result = handler.classify_stream_error(None, had_tool_events=False, final_text=None)

    assert result is None


def test_classify_stream_error_tool_events_no_final_returns_no_final_fallback() -> None:
    """Tool events received but no final text → non-None 'no final' fallback string."""
    handler = _make_handler()

    result = handler.classify_stream_error(None, had_tool_events=True, final_text=None)

    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# guard() — log level discipline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_logs_at_debug_not_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """guard() must log at DEBUG on exception paths — never WARNING/ERROR/EXCEPTION.

    Bus-bound paths must not leak via log channels (same sanitization discipline
    applies to log verbosity: DEBUG keeps it out of production log aggregators).
    """
    handler = _make_handler()

    async def always_fails() -> None:
        raise RuntimeError("internal detail that must not surface")

    with caplog.at_level(logging.DEBUG, logger="factory.outbound.error_handler"):
        await handler.guard(always_fails, context="log_test")

    # At least one record must be emitted (confirms the code path was hit).
    outbound_records = [
        r for r in caplog.records if r.name == "factory.outbound.error_handler"
    ]
    assert outbound_records, "guard() must emit at least one log record on exception"

    # None of the records may be above DEBUG.
    for record in outbound_records:
        assert record.levelno <= logging.DEBUG, (
            f"guard() logged at {record.levelname} — expected DEBUG only"
        )
