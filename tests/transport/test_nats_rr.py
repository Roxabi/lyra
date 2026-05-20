"""S2 RED tests — NatsTransport.call() error paths return Result, never raise.

Spec: SC-07 (sanitization), SC-15 (transport suite passes), B1 consensus.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import nats.errors
import pytest

from lyra.transport._result import Err, Ok
from lyra.transport.nats_request_response import NatsTransport


@pytest.mark.asyncio
async def test_call_success_returns_ok():
    nc = AsyncMock()
    reply = MagicMock()
    reply.data = b"hello"
    nc.request = AsyncMock(return_value=reply)
    t = NatsTransport(nc)
    result = await t.call("subj", b"payload")
    assert isinstance(result, Ok)
    assert result.value == b"hello"


@pytest.mark.asyncio
async def test_call_timeout_returns_err_never_raises():
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=TimeoutError())
    t = NatsTransport(nc)
    result = await t.call("subj", b"x")
    assert isinstance(result, Err)
    assert result.error.code == "transport.timeout"
    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_call_no_responders_returns_err():
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=nats.errors.NoRespondersError())
    t = NatsTransport(nc)
    result = await t.call("subj", b"x")
    assert isinstance(result, Err)
    assert result.error.code == "transport.no_responders"


@pytest.mark.asyncio
async def test_call_payload_too_large_returns_err():
    """MaxPayloadError → transport.payload_too_large via isinstance, ¬str(exc)."""
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=nats.errors.MaxPayloadError())
    t = NatsTransport(nc)
    result = await t.call("subj", b"x" * 1024)
    assert isinstance(result, Err)
    assert result.error.code == "transport.payload_too_large"
    assert result.error.retryable is False
    # message must be class name only, never str(exc) leak
    assert result.error.message == "MaxPayloadError"


@pytest.mark.asyncio
async def test_call_sanitized_error_no_str_exc():
    """SanitizedError.message = type(exc).__name__ only — never str(exc) leak."""
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=TimeoutError("internal-secret-state"))
    t = NatsTransport(nc)
    result = await t.call("subj", b"x")
    assert isinstance(result, Err)
    assert "internal-secret-state" not in result.error.message
    assert result.error.message == "TimeoutError"


@pytest.mark.asyncio
async def test_open_inbox_cleanup_on_early_break():
    # B1 consensus: early break must trigger CM __aexit__ → unsubscribe.
    nc = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.data = b"chunk1"
    mock_sub = AsyncMock()
    mock_sub.next_msg = AsyncMock(return_value=mock_msg)
    nc.subscribe = AsyncMock(return_value=mock_sub)
    nc.new_inbox = MagicMock(return_value="_INBOX.test")

    t = NatsTransport(nc)
    async with t.open_inbox() as stream:  # type: ignore[attr-defined]
        async for _ in stream.messages:
            break

    mock_sub.unsubscribe.assert_called_once()


@pytest.mark.asyncio
async def test_open_inbox_yields_inbox_stream():
    """CM yields InboxStream with inbox_subject set from nc.new_inbox()."""
    from lyra.transport._result import InboxStream

    nc = AsyncMock()
    mock_sub = AsyncMock()
    mock_sub.next_msg = AsyncMock(side_effect=TimeoutError())
    nc.subscribe = AsyncMock(return_value=mock_sub)
    nc.new_inbox = MagicMock(return_value="_INBOX.abc123")

    t = NatsTransport(nc)
    async with t.open_inbox() as stream:  # type: ignore[attr-defined]
        assert isinstance(stream, InboxStream)
        assert stream.inbox_subject == "_INBOX.abc123"
