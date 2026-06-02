"""Tests for _send_with_retry — retry loop extracted from _dispatch.py.

Covers:
- success on first attempt
- retry count = 4 (1 initial + 3 retries)
- backoff delays (1.0, 2.0, 4.0)
- transient error triggers retry, fatal error aborts
- BaseException re-raised immediately
- streaming excluded from retry
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.core.hub.outbound._dispatch import _send_with_retry
from factory.core.messaging.message import OutboundMessage
from tests.core.conftest import make_dispatcher_msg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.send = AsyncMock()
    adapter.send_streaming = AsyncMock()
    adapter.render_audio = AsyncMock()
    adapter.render_audio_stream = AsyncMock()
    adapter.render_voice_stream = AsyncMock()
    adapter.render_attachment = AsyncMock()
    return adapter


def _make_circuit() -> MagicMock:
    circuit = MagicMock()
    circuit.record_success = MagicMock()
    circuit.record_failure = MagicMock()
    return circuit


def _make_sleep_tracker() -> tuple[list[float], object]:
    sleeps: list[float] = []

    async def _track(delay: float) -> None:
        sleeps.append(delay)

    return sleeps, _track


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_success_first_attempt() -> None:
    """Successful send on first attempt returns None and records success."""
    adapter = _make_adapter()
    circuit = _make_circuit()
    msg = make_dispatcher_msg()
    payload = OutboundMessage.from_text("hi")

    result = await _send_with_retry(
        platform_name="telegram",
        adapter=adapter,
        circuit=circuit,
        kind="send",
        msg=msg,
        payload=payload,
    )

    assert result is None
    adapter.send.assert_awaited_once_with(msg, payload)
    circuit.record_success.assert_called_once()
    circuit.record_failure.assert_not_called()


# ---------------------------------------------------------------------------
# Retry exhaustion and backoff
# ---------------------------------------------------------------------------


async def test_retry_exhausts_four_attempts() -> None:
    """Transient error on all 4 attempts; adapter called exactly 4 times."""
    adapter = _make_adapter()
    adapter.send = AsyncMock(side_effect=ConnectionError("timeout"))
    circuit = _make_circuit()
    msg = make_dispatcher_msg()
    payload = OutboundMessage.from_text("hi")
    sleeps, fast_sleep = _make_sleep_tracker()

    with patch("asyncio.sleep", new=fast_sleep):
        result = await _send_with_retry(
            platform_name="telegram",
            adapter=adapter,
            circuit=circuit,
            kind="send",
            msg=msg,
            payload=payload,
        )

    assert isinstance(result, ConnectionError)
    assert adapter.send.await_count == 4
    assert sleeps == [1.0, 2.0, 4.0]
    circuit.record_success.assert_not_called()


async def test_success_on_fourth_attempt() -> None:
    """Succeeds on 4th attempt after 3 transient failures."""
    adapter = _make_adapter()
    call_count = 0

    async def flaky_send(*_args: object, **_kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count < 4:
            raise ConnectionError("timeout")

    adapter.send = AsyncMock(side_effect=flaky_send)
    circuit = _make_circuit()
    msg = make_dispatcher_msg()
    payload = OutboundMessage.from_text("hi")
    sleeps, fast_sleep = _make_sleep_tracker()

    with patch("asyncio.sleep", new=fast_sleep):
        result = await _send_with_retry(
            platform_name="telegram",
            adapter=adapter,
            circuit=circuit,
            kind="send",
            msg=msg,
            payload=payload,
        )

    assert result is None
    assert call_count == 4
    assert sleeps == [1.0, 2.0, 4.0]
    circuit.record_success.assert_called_once()


# ---------------------------------------------------------------------------
# Transient vs fatal
# ---------------------------------------------------------------------------


async def test_transient_retried_fatal_aborts() -> None:
    """ConnectionError triggers retry; ValueError aborts immediately."""
    adapter = _make_adapter()
    circuit = _make_circuit()
    msg = make_dispatcher_msg()
    payload = OutboundMessage.from_text("hi")

    # Transient path
    adapter.send = AsyncMock(side_effect=ConnectionError("timeout"))
    _, fast_sleep = _make_sleep_tracker()
    with patch("asyncio.sleep", new=fast_sleep):
        result = await _send_with_retry(
            platform_name="telegram",
            adapter=adapter,
            circuit=circuit,
            kind="send",
            msg=msg,
            payload=payload,
        )
    assert isinstance(result, ConnectionError)
    assert adapter.send.await_count == 4

    # Fatal path
    adapter.send.reset_mock()
    circuit.reset_mock()
    adapter.send = AsyncMock(side_effect=ValueError("bad request"))
    result = await _send_with_retry(
        platform_name="telegram",
        adapter=adapter,
        circuit=circuit,
        kind="send",
        msg=msg,
        payload=payload,
    )
    assert isinstance(result, ValueError)
    adapter.send.assert_awaited_once()
    circuit.record_success.assert_not_called()


# ---------------------------------------------------------------------------
# BaseException propagation
# ---------------------------------------------------------------------------


async def test_base_exception_re_raised_immediately() -> None:
    """CancelledError is re-raised immediately and circuit records failure."""
    adapter = _make_adapter()
    adapter.send = AsyncMock(side_effect=asyncio.CancelledError("cancelled"))
    circuit = _make_circuit()
    msg = make_dispatcher_msg()
    payload = OutboundMessage.from_text("hi")

    with pytest.raises(asyncio.CancelledError):
        await _send_with_retry(
            platform_name="telegram",
            adapter=adapter,
            circuit=circuit,
            kind="send",
            msg=msg,
            payload=payload,
        )

    adapter.send.assert_awaited_once()
    circuit.record_failure.assert_called_once()
    circuit.record_success.assert_not_called()


# ---------------------------------------------------------------------------
# Streaming exclusion
# ---------------------------------------------------------------------------


async def test_streaming_excluded_from_retry() -> None:
    """Streaming errors are not retried; adapter called exactly once."""
    adapter = _make_adapter()
    adapter.send_streaming = AsyncMock(side_effect=ConnectionError("timeout"))
    circuit = _make_circuit()
    msg = make_dispatcher_msg()

    async def chunks() -> AsyncIterator[str]:
        yield "chunk1"

    result = await _send_with_retry(
        platform_name="telegram",
        adapter=adapter,
        circuit=circuit,
        kind="streaming",
        msg=msg,
        payload=chunks(),
        outbound=OutboundMessage.from_text("hi"),
    )

    assert isinstance(result, ConnectionError)
    adapter.send_streaming.assert_awaited_once()
    circuit.record_success.assert_not_called()
