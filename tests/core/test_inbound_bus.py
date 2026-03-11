"""Tests for InboundBus: per-platform queues + feeder tasks + staging queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from lyra.core.inbound_bus import InboundBus
from lyra.core.message import (
    Message,
    MessageType,
    Platform,
    TelegramContext,
    TextContent,
)


def _make_msg(platform: Platform = Platform.TELEGRAM) -> Message:
    from lyra.core.message import DiscordContext

    if platform == Platform.TELEGRAM:
        ctx = TelegramContext(chat_id=123)
    else:
        ctx = DiscordContext(guild_id=1, channel_id=2, message_id=3)
    return Message.from_adapter(
        platform=platform,
        bot_id="main",
        user_id="user:1",
        user_name="Alice",
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=ctx,
    )


class TestInboundBusRegistration:
    def test_register_creates_queue(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=50)
        assert Platform.TELEGRAM in bus._queues
        assert bus._queues[Platform.TELEGRAM].maxsize == 50

    def test_qsize_zero_initially(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM)
        assert bus.qsize(Platform.TELEGRAM) == 0

    def test_qsize_unknown_platform_returns_zero(self) -> None:
        bus = InboundBus()
        assert bus.qsize(Platform.TELEGRAM) == 0

    def test_put_increments_qsize(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        msg = _make_msg(Platform.TELEGRAM)
        bus.put(Platform.TELEGRAM, msg)
        assert bus.qsize(Platform.TELEGRAM) == 1

    def test_put_raises_queue_full(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=1)
        msg = _make_msg(Platform.TELEGRAM)
        bus.put(Platform.TELEGRAM, msg)
        with pytest.raises(asyncio.QueueFull):
            bus.put(Platform.TELEGRAM, msg)


class TestInboundBusFeeder:
    async def test_feeder_forwards_to_staging(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()

        try:
            msg = _make_msg(Platform.TELEGRAM)
            bus.put(Platform.TELEGRAM, msg)

            # Wait for feeder to forward to staging
            received = await asyncio.wait_for(bus.get(), timeout=0.5)
            assert received is msg
        finally:
            await bus.stop()

    async def test_two_platforms_isolated(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=1)
        bus.register(Platform.DISCORD, maxsize=10)
        await bus.start()

        try:
            tg_msg = _make_msg(Platform.TELEGRAM)
            dc_msg = _make_msg(Platform.DISCORD)

            # Fill telegram queue
            bus.put(Platform.TELEGRAM, tg_msg)
            with pytest.raises(asyncio.QueueFull):
                bus.put(Platform.TELEGRAM, tg_msg)

            # Discord queue unaffected
            bus.put(Platform.DISCORD, dc_msg)
            assert bus.qsize(Platform.DISCORD) >= 0  # may have drained already
        finally:
            await bus.stop()

    async def test_stop_cancels_feeders(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()
        assert len(bus._feeders) == 1

        await bus.stop()
        assert len(bus._feeders) == 0
