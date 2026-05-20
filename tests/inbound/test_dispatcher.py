"""Tests for Dispatcher.dispatch — inbound pipeline tail stage."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage
from lyra.inbound.context import DispatchCtx
from lyra.inbound.dispatcher import Dispatcher

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg() -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="user:42",
        user_name="testuser",
        is_mention=False,
        text="hello",
        text_raw="hello",
        trust_level=TrustLevel.PUBLIC,
    )


def _make_ctx(
    *,
    inbound_bus: object,
    circuit_registry: object = None,
    outbound_listener: object = None,
) -> DispatchCtx:
    typing = MagicMock()
    return DispatchCtx(
        inbound_bus=inbound_bus,  # type: ignore[arg-type]
        circuit_registry=circuit_registry,  # type: ignore[arg-type]
        outbound_listener=outbound_listener,  # type: ignore[arg-type]
        typing=typing,
        msg_catalog=None,
    )


def _open_circuit_registry() -> MagicMock:
    """Return a CircuitRegistry mock whose 'hub' circuit reports is_open=True."""
    cb = MagicMock()
    cb.is_open.return_value = True
    registry = MagicMock()
    registry.get.return_value = cb
    return registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDispatcher:
    """Dispatcher.dispatch — happy path and backpressure guards."""

    @pytest.mark.asyncio
    async def test_dispatch_normal_enqueues(self) -> None:
        # Arrange
        bus = AsyncMock()
        ctx = _make_ctx(inbound_bus=bus, circuit_registry=None)
        msg = _make_msg()
        send_backpressure = AsyncMock()
        on_drop = MagicMock()

        dispatcher = Dispatcher()

        # Act
        await dispatcher.dispatch(msg, ctx, send_backpressure, on_drop)

        # Assert — bus.put called once; no drop or backpressure signalled.
        # Negative: deleting the bus.put call in dispatch makes this fail.
        bus.put.assert_awaited_once()
        on_drop.assert_not_called()
        send_backpressure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_circuit_open_calls_on_drop_and_backpressure(self) -> None:
        # Arrange
        bus = AsyncMock()
        registry = _open_circuit_registry()
        ctx = _make_ctx(inbound_bus=bus, circuit_registry=registry)
        msg = _make_msg()
        send_backpressure = AsyncMock()
        on_drop = MagicMock()

        dispatcher = Dispatcher()

        # Act
        await dispatcher.dispatch(msg, ctx, send_backpressure, on_drop)

        # Assert — circuit-open path: bus never enqueued, drop + backpressure fired.
        # Negative: removing the cb.is_open() guard lets bus.put be called instead.
        bus.put.assert_not_awaited()
        on_drop.assert_called_once()
        send_backpressure.assert_awaited_once()
        text_sent: str = send_backpressure.call_args[0][0]
        assert isinstance(text_sent, str) and len(text_sent) > 0

    @pytest.mark.asyncio
    async def test_dispatch_queue_full_calls_on_drop_and_backpressure(self) -> None:
        # Arrange — bus.put raises QueueFull to simulate a saturated queue.
        bus = AsyncMock()
        bus.put.side_effect = asyncio.QueueFull
        ctx = _make_ctx(inbound_bus=bus, circuit_registry=None)
        msg = _make_msg()
        send_backpressure = AsyncMock()
        on_drop = MagicMock()

        dispatcher = Dispatcher()

        # Act — must NOT re-raise; dispatch returns normally.
        await dispatcher.dispatch(msg, ctx, send_backpressure, on_drop)

        # Assert — QueueFull path: drop + backpressure fired; no exception escapes.
        # Negative: removing the QueueFull except block lets the exception propagate.
        on_drop.assert_called_once()
        send_backpressure.assert_awaited_once()
        text_sent = send_backpressure.call_args[0][0]
        assert isinstance(text_sent, str) and len(text_sent) > 0
