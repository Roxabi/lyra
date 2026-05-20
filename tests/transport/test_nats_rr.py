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
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=nats.errors.Error("max_payload exceeded"))
    t = NatsTransport(nc)
    result = await t.call("subj", b"x" * 1024)
    assert isinstance(result, Err)
    assert result.error.code == "transport.payload_too_large"
    assert result.error.retryable is False


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
