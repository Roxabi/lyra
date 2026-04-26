"""Tests for LocalBus: per-platform queues + feeder tasks + staging queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.inbound_bus import LocalBus
from lyra.core.messaging.message import (
    DiscordMeta,
    InboundMessage,
    Platform,
    TelegramMeta,
)
from tests.conftest import TIMEOUT_FAST


def _make_msg(platform: Platform = Platform.TELEGRAM) -> InboundMessage:
    if platform == Platform.TELEGRAM:
        scope = "chat:123"
        meta: TelegramMeta | DiscordMeta = TelegramMeta(
            chat_id=123, topic_id=None, message_id=None, is_group=False
        )
    else:
        scope = "channel:2"
        meta = DiscordMeta(
            guild_id=1,
            channel_id=2,
            message_id=3,
            thread_id=None,
            channel_type="text",
        )
    return InboundMessage(
        id="msg-1",
        platform=platform.value,
        bot_id="main",
        scope_id=scope,
        user_id="user:1",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta=meta,
        trust_level=TrustLevel.TRUSTED,
    )


class TestInboundBusRegistration:
    def test_register_creates_queue(self) -> None:
        bus = LocalBus()
        bus.register(Platform.TELEGRAM, maxsize=50)
        assert Platform.TELEGRAM in bus._queues
        assert bus._queues[Platform.TELEGRAM].maxsize == 50

    def test_qsize_zero_initially(self) -> None:
        bus = LocalBus()
        bus.register(Platform.TELEGRAM)
        assert bus.qsize(Platform.TELEGRAM) == 0

    def test_qsize_unknown_platform_returns_zero(self) -> None:
        bus = LocalBus()
        assert bus.qsize(Platform.TELEGRAM) == 0

    async def test_put_increments_qsize(self) -> None:
        bus = LocalBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        msg = _make_msg(Platform.TELEGRAM)
        await bus.put(Platform.TELEGRAM, msg)
        assert bus.qsize(Platform.TELEGRAM) == 1

    async def test_put_raises_queue_full(self) -> None:
        bus = LocalBus()
        bus.register(Platform.TELEGRAM, maxsize=1)
        msg = _make_msg(Platform.TELEGRAM)
        await bus.put(Platform.TELEGRAM, msg)
        with pytest.raises(asyncio.QueueFull):
            await bus.put(Platform.TELEGRAM, msg)


class TestInboundBusFeeder:
    async def test_feeder_forwards_to_staging(self) -> None:
        bus = LocalBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()

        try:
            msg = _make_msg(Platform.TELEGRAM)
            await bus.put(Platform.TELEGRAM, msg)

            # Wait for feeder to forward to staging
            received = await asyncio.wait_for(bus.get(), timeout=TIMEOUT_FAST)
            assert received is msg
        finally:
            await bus.stop()

    async def test_two_platforms_isolated(self) -> None:
        bus = LocalBus()
        bus.register(Platform.TELEGRAM, maxsize=1)
        bus.register(Platform.DISCORD, maxsize=10)
        await bus.start()

        try:
            tg_msg = _make_msg(Platform.TELEGRAM)
            dc_msg = _make_msg(Platform.DISCORD)

            # Fill telegram queue
            await bus.put(Platform.TELEGRAM, tg_msg)
            with pytest.raises(asyncio.QueueFull):
                await bus.put(Platform.TELEGRAM, tg_msg)

            # Discord queue unaffected — put succeeds (not raises QueueFull)
            await bus.put(Platform.DISCORD, dc_msg)
        finally:
            await bus.stop()

    async def test_stop_cancels_feeders(self) -> None:
        bus = LocalBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()
        assert len(bus._feeders) == 1

        await bus.stop()
        assert len(bus._feeders) == 0

    async def test_double_start_raises(self) -> None:
        bus = LocalBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()

        try:
            with pytest.raises(RuntimeError, match="already running"):
                await bus.start()
        finally:
            await bus.stop()
