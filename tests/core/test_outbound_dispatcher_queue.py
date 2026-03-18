"""Tests for OutboundDispatcher: enqueue / streaming / circuit-breaker behaviour."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from lyra.core.circuit_breaker import CircuitBreaker
from lyra.core.message import OutboundMessage
from lyra.core.outbound_dispatcher import OutboundDispatcher

from .conftest import make_dispatcher_msg


def _make_adapter() -> tuple[MagicMock, OutboundDispatcher]:
    adapter = MagicMock()
    adapter.send = AsyncMock()
    adapter.send_streaming = AsyncMock()
    adapter.render_audio = AsyncMock()
    adapter.render_attachment = AsyncMock()
    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
    return adapter, dispatcher


class TestOutboundDispatcherEnqueue:
    async def test_enqueue_delivers_via_adapter(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            outbound = OutboundMessage.from_text("hi")
            dispatcher.enqueue(msg, outbound)
            # Wait for worker to process
            await asyncio.sleep(0.05)
            adapter.send.assert_awaited_once_with(msg, outbound)
        finally:
            await dispatcher.stop()

    async def test_enqueue_streaming_delivers_via_adapter(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()

            async def chunks() -> AsyncIterator[str]:
                yield "hello"

            dispatcher.enqueue_streaming(msg, chunks())
            await asyncio.sleep(0.05)
            adapter.send_streaming.assert_awaited_once()
            call_args = adapter.send_streaming.call_args
            assert call_args[0][0] is msg
        finally:
            await dispatcher.stop()

    async def test_enqueue_streaming_forwards_outbound(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            outbound = OutboundMessage.from_text("")

            async def chunks() -> AsyncIterator[str]:
                yield "hello"

            dispatcher.enqueue_streaming(msg, chunks(), outbound)
            await asyncio.sleep(0.05)
            adapter.send_streaming.assert_awaited_once()
            call_args = adapter.send_streaming.call_args
            assert call_args[0][0] is msg
            assert call_args[0][2] is outbound
        finally:
            await dispatcher.stop()

    async def test_qsize_reflects_pending_items(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock(side_effect=lambda *_: asyncio.sleep(1))
        dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            # Enqueue 3 items quickly — worker is blocked on the first
            dispatcher.enqueue(msg, OutboundMessage.from_text("1"))
            dispatcher.enqueue(msg, OutboundMessage.from_text("2"))
            dispatcher.enqueue(msg, OutboundMessage.from_text("3"))
            assert dispatcher.qsize() >= 2  # 3 enqueued, worker blocked on 1st
        finally:
            await dispatcher.stop()


class TestOutboundDispatcherCircuitBreaker:
    async def test_open_circuit_drops_message(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock()

        cb = CircuitBreaker(name="telegram", failure_threshold=1)
        cb.record_failure()  # trip the circuit
        assert cb.is_open()

        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
            await asyncio.sleep(0.05)
            # Circuit is open — adapter.send should NOT be called
            adapter.send.assert_not_awaited()
        finally:
            await dispatcher.stop()

    async def test_open_circuit_drops_streaming_and_sets_sentinel(self) -> None:
        adapter = MagicMock()
        adapter.send_streaming = AsyncMock()

        cb = CircuitBreaker(name="telegram", failure_threshold=1)
        cb.record_failure()
        assert cb.is_open()

        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            outbound = OutboundMessage.from_text("")

            async def chunks() -> AsyncIterator[str]:
                yield "hello"

            dispatcher.enqueue_streaming(msg, chunks(), outbound)
            await asyncio.sleep(0.05)
            adapter.send_streaming.assert_not_awaited()
            assert outbound.metadata["reply_message_id"] is None
        finally:
            await dispatcher.stop()

    async def test_successful_send_records_cb_success(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock()

        cb = CircuitBreaker(name="telegram", failure_threshold=5)
        # Put in half-open state: open then let recovery time elapse (mock)
        from lyra.core.circuit_breaker import CircuitState

        cb._state = CircuitState.HALF_OPEN

        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
            await asyncio.sleep(0.05)
            adapter.send.assert_awaited_once()
            # CB should be closed after successful send
            from lyra.core.circuit_breaker import CircuitState

            assert cb._state == CircuitState.CLOSED
        finally:
            await dispatcher.stop()

    async def test_failed_send_records_cb_failure(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock(side_effect=Exception("network error"))

        cb = CircuitBreaker(name="telegram", failure_threshold=5)
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
            await asyncio.sleep(0.05)
            assert cb._failure_count >= 1
        finally:
            await dispatcher.stop()

    async def test_stop_cancels_worker(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        assert dispatcher._worker is not None
        await dispatcher.stop()
        assert dispatcher._worker is None


# ---------------------------------------------------------------------------
# RED — #138: OutboundDispatcher accepts OutboundMessage (Slice V2)
# ---------------------------------------------------------------------------


def test_enqueue_accepts_outbound_message() -> None:
    """OutboundDispatcher.enqueue() must accept an OutboundMessage payload
    without raising TypeError (issue #138, U3)."""
    from lyra.core.message import OutboundMessage

    # Arrange
    adapter = MagicMock()
    adapter.send = AsyncMock()
    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
    mock_msg = make_dispatcher_msg()
    outbound = OutboundMessage.from_text("test")

    # Act / Assert — must not raise TypeError
    dispatcher.enqueue(mock_msg, outbound)
