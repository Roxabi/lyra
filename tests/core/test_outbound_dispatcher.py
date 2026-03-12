"""Tests for OutboundDispatcher: per-platform outbound queue with CB ownership."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from lyra.core.auth import TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker
from lyra.core.message import (
    Message,
    MessageType,
    Platform,
    Response,
    TelegramContext,
    TextContent,
)
from lyra.core.outbound_dispatcher import OutboundDispatcher


def _make_msg() -> Message:
    return Message.from_adapter(
        platform=Platform.TELEGRAM,
        bot_id="main",
        user_id="tg:user:42",
        user_name="Alice",
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_context=TelegramContext(chat_id=123),
    )


def _make_adapter() -> tuple[MagicMock, OutboundDispatcher]:
    adapter = MagicMock()
    adapter.send = AsyncMock()
    adapter.send_streaming = AsyncMock()
    dispatcher = OutboundDispatcher(
        platform_name="telegram", adapter=adapter
    )
    return adapter, dispatcher


class TestOutboundDispatcherEnqueue:
    async def test_enqueue_delivers_via_adapter(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        try:
            msg = _make_msg()
            response = Response(content="hi")
            dispatcher.enqueue(msg, response)
            # Wait for worker to process
            await asyncio.sleep(0.05)
            adapter.send.assert_awaited_once_with(msg, response)
        finally:
            await dispatcher.stop()

    async def test_enqueue_streaming_delivers_via_adapter(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        try:
            msg = _make_msg()

            async def chunks() -> AsyncIterator[str]:
                yield "hello"

            dispatcher.enqueue_streaming(msg, chunks())
            await asyncio.sleep(0.05)
            adapter.send_streaming.assert_awaited_once()
        finally:
            await dispatcher.stop()

    async def test_qsize_reflects_pending_items(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock(side_effect=lambda *_: asyncio.sleep(1))
        dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
        await dispatcher.start()
        try:
            msg = _make_msg()
            # Enqueue 3 items quickly — worker is blocked on the first
            dispatcher.enqueue(msg, Response(content="1"))
            dispatcher.enqueue(msg, Response(content="2"))
            dispatcher.enqueue(msg, Response(content="3"))
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
            msg = _make_msg()
            dispatcher.enqueue(msg, Response(content="hi"))
            await asyncio.sleep(0.05)
            # Circuit is open — adapter.send should NOT be called
            adapter.send.assert_not_awaited()
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
            msg = _make_msg()
            dispatcher.enqueue(msg, Response(content="hi"))
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
            msg = _make_msg()
            dispatcher.enqueue(msg, Response(content="hi"))
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
