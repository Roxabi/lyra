"""Tests for RoutingContext — issue #152.

Covers:
- RoutingContext creation and immutability
- Population in TelegramAdapter.normalize() and DiscordAdapter.normalize()
- Propagation from InboundMessage → Response → OutboundMessage
- Verification in OutboundDispatcher
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth import TrustLevel
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    Response,
    RoutingContext,
)

# ---------------------------------------------------------------------------
# RoutingContext dataclass
# ---------------------------------------------------------------------------


class TestRoutingContext:
    def test_importable_from_core(self) -> None:
        from lyra.core import RoutingContext as RC  # noqa: F401

        assert RC is RoutingContext

    def test_creation(self) -> None:
        rc = RoutingContext(
            platform="telegram",
            bot_id="main",
            scope_id="chat:123",
        )
        assert rc.platform == "telegram"
        assert rc.bot_id == "main"
        assert rc.scope_id == "chat:123"
        assert rc.thread_id is None
        assert rc.reply_to_message_id is None
        assert rc.platform_meta == {}

    def test_frozen(self) -> None:
        rc = RoutingContext(platform="telegram", bot_id="main", scope_id="chat:123")
        with pytest.raises(AttributeError):
            rc.platform = "discord"  # type: ignore[misc]

    def test_with_all_fields(self) -> None:
        meta = {"chat_id": 123, "topic_id": 456}
        rc = RoutingContext(
            platform="telegram",
            bot_id="main",
            scope_id="chat:123:topic:456",
            thread_id="456",
            reply_to_message_id="789",
            platform_meta=meta,
        )
        assert rc.thread_id == "456"
        assert rc.reply_to_message_id == "789"
        assert rc.platform_meta is meta


# ---------------------------------------------------------------------------
# InboundMessage carries routing
# ---------------------------------------------------------------------------


def _make_inbound(routing: RoutingContext | None = None) -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta={"chat_id": 123},
        routing=routing,
    )


class TestInboundMessageRouting:
    def test_default_none(self) -> None:
        msg = _make_inbound()
        assert msg.routing is None

    def test_with_routing(self) -> None:
        rc = RoutingContext(platform="telegram", bot_id="main", scope_id="chat:123")
        msg = _make_inbound(routing=rc)
        assert msg.routing is rc


# ---------------------------------------------------------------------------
# Response → OutboundMessage propagation
# ---------------------------------------------------------------------------


class TestResponseRoutingPropagation:
    def test_response_routing_default_none(self) -> None:
        r = Response(content="hi")
        assert r.routing is None

    def test_to_outbound_propagates_routing(self) -> None:
        rc = RoutingContext(platform="telegram", bot_id="main", scope_id="chat:123")
        r = Response(content="hi", routing=rc)
        outbound = r.to_outbound()
        assert outbound.routing is rc

    def test_to_outbound_none_routing(self) -> None:
        r = Response(content="hi")
        outbound = r.to_outbound()
        assert outbound.routing is None


# ---------------------------------------------------------------------------
# OutboundMessage routing field
# ---------------------------------------------------------------------------


class TestOutboundMessageRouting:
    def test_default_none(self) -> None:
        om = OutboundMessage.from_text("hi")
        assert om.routing is None

    def test_assignable(self) -> None:
        rc = RoutingContext(platform="discord", bot_id="main", scope_id="channel:456")
        om = OutboundMessage.from_text("hi")
        om.routing = rc
        assert om.routing is rc


# ---------------------------------------------------------------------------
# TelegramAdapter.normalize() populates routing
# ---------------------------------------------------------------------------


class TestTelegramNormalizeRouting:
    def test_routing_populated(self) -> None:
        from lyra.adapters.telegram import _ALLOW_ALL, TelegramAdapter

        hub = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="fake",
            hub=hub,
            auth=_ALLOW_ALL,
        )

        raw = SimpleNamespace(
            from_user=SimpleNamespace(id=42, full_name="Alice"),
            chat=SimpleNamespace(id=123, type="private"),
            text="hello",
            date=datetime.now(timezone.utc),
            message_id=999,
            message_thread_id=None,
            entities=None,
            photo=None,
            document=None,
            video=None,
            animation=None,
            sticker=None,
            caption=None,
        )

        msg = adapter.normalize(raw)
        assert msg.routing is not None
        assert msg.routing.platform == "telegram"
        assert msg.routing.bot_id == "main"
        assert msg.routing.scope_id == "chat:123"
        assert msg.routing.thread_id is None
        assert msg.routing.reply_to_message_id == "999"

    def test_routing_with_topic(self) -> None:
        from lyra.adapters.telegram import _ALLOW_ALL, TelegramAdapter

        hub = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="fake",
            hub=hub,
            auth=_ALLOW_ALL,
        )

        raw = SimpleNamespace(
            from_user=SimpleNamespace(id=42, full_name="Alice"),
            chat=SimpleNamespace(id=123, type="supergroup"),
            text="hello",
            date=datetime.now(timezone.utc),
            message_id=999,
            message_thread_id=456,
            entities=None,
            photo=None,
            document=None,
            video=None,
            animation=None,
            sticker=None,
            caption=None,
        )

        msg = adapter.normalize(raw)
        assert msg.routing is not None
        assert msg.routing.scope_id == "chat:123:topic:456"
        assert msg.routing.thread_id == "456"


# ---------------------------------------------------------------------------
# DiscordAdapter.normalize() populates routing
# ---------------------------------------------------------------------------


class TestDiscordNormalizeRouting:
    def test_routing_populated(self) -> None:
        from lyra.adapters.discord import _ALLOW_ALL, DiscordAdapter

        hub = MagicMock()
        adapter = DiscordAdapter.__new__(DiscordAdapter)
        adapter._hub = hub
        adapter._bot_id = "main"
        adapter._bot_user = None
        adapter._mention_re = None
        adapter._auth = _ALLOW_ALL

        channel = MagicMock(spec=[])
        channel.id = 789

        raw = SimpleNamespace(
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            channel=channel,
            guild=SimpleNamespace(id=100),
            content="hello",
            created_at=datetime.now(timezone.utc),
            id=999,
            mentions=[],
            attachments=[],
        )

        msg = adapter.normalize(raw)
        assert msg.routing is not None
        assert msg.routing.platform == "discord"
        assert msg.routing.bot_id == "main"
        assert msg.routing.scope_id == "channel:789"
        assert msg.routing.thread_id is None
        assert msg.routing.reply_to_message_id == "999"


# ---------------------------------------------------------------------------
# OutboundDispatcher routing verification
# ---------------------------------------------------------------------------


class TestDispatcherRoutingVerification:
    async def test_matching_routing_passes(self) -> None:
        from lyra.core.outbound_dispatcher import OutboundDispatcher

        adapter = MagicMock()
        adapter.send = AsyncMock()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            rc = RoutingContext(
                platform="telegram", bot_id="main", scope_id="chat:123"
            )
            msg = _make_inbound(routing=rc)
            outbound = OutboundMessage.from_text("hi")
            outbound.routing = rc
            dispatcher.enqueue(msg, outbound)
            await asyncio.sleep(0.05)
            adapter.send.assert_awaited_once()
        finally:
            await dispatcher.stop()

    async def test_mismatched_platform_drops(self) -> None:
        from lyra.core.outbound_dispatcher import OutboundDispatcher

        adapter = MagicMock()
        adapter.send = AsyncMock()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            rc = RoutingContext(
                platform="discord", bot_id="main", scope_id="channel:456"
            )
            msg = _make_inbound(routing=rc)
            outbound = OutboundMessage.from_text("hi")
            outbound.routing = rc
            dispatcher.enqueue(msg, outbound)
            await asyncio.sleep(0.05)
            adapter.send.assert_not_awaited()
        finally:
            await dispatcher.stop()

    async def test_mismatched_bot_id_drops(self) -> None:
        from lyra.core.outbound_dispatcher import OutboundDispatcher

        adapter = MagicMock()
        adapter.send = AsyncMock()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            rc = RoutingContext(
                platform="telegram", bot_id="other-bot", scope_id="chat:123"
            )
            msg = _make_inbound(routing=rc)
            outbound = OutboundMessage.from_text("hi")
            outbound.routing = rc
            dispatcher.enqueue(msg, outbound)
            await asyncio.sleep(0.05)
            adapter.send.assert_not_awaited()
        finally:
            await dispatcher.stop()

    async def test_none_routing_passes(self) -> None:
        """Backward compat: no routing context → message is delivered."""
        from lyra.core.outbound_dispatcher import OutboundDispatcher

        adapter = MagicMock()
        adapter.send = AsyncMock()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            msg = _make_inbound()  # routing=None
            outbound = OutboundMessage.from_text("hi")
            dispatcher.enqueue(msg, outbound)
            await asyncio.sleep(0.05)
            adapter.send.assert_awaited_once()
        finally:
            await dispatcher.stop()


# ---------------------------------------------------------------------------
# Hub dispatch propagation
# ---------------------------------------------------------------------------


class TestHubDispatchPropagation:
    async def test_dispatch_response_propagates_routing(self) -> None:
        from lyra.core.hub import Hub

        hub = Hub()
        adapter = MagicMock()
        adapter.send = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)

        rc = RoutingContext(platform="telegram", bot_id="main", scope_id="chat:123")
        msg = _make_inbound(routing=rc)
        response = Response(content="hi")
        await hub.dispatch_response(msg, response)

        # The adapter.send should have been called with an OutboundMessage
        # that has routing propagated from the InboundMessage
        call_args = adapter.send.call_args
        outbound = call_args[0][1]
        assert outbound.routing is rc
