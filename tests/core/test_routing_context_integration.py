"""Routing context — adapter normalization, dispatcher verification, hub propagation.

Covers:
- TelegramAdapter.normalize() populates routing
- DiscordAdapter.normalize() populates routing
- OutboundDispatcher._verify_routing (direct unit tests)
- OutboundDispatcher integration (queue-based, deterministic drain)
- Hub dispatch propagation (response + streaming)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from lyra.core.message import (
    OutboundMessage,
    Platform,
    Response,
    RoutingContext,
)
from lyra.core.render_events import TextRenderEvent

from .conftest import _RC_DC, _RC_TG, make_routing_inbound

# ---------------------------------------------------------------------------
# TelegramAdapter.normalize() populates routing
# ---------------------------------------------------------------------------


class TestTelegramNormalizeRouting:
    def test_routing_populated(self) -> None:
        from lyra.adapters.telegram import _ALLOW_ALL, TelegramAdapter

        adapter = TelegramAdapter(
            bot_id="main",
            token="fake",
            inbound_bus=MagicMock(),
            inbound_audio_bus=MagicMock(),
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

        adapter = TelegramAdapter(
            bot_id="main",
            token="fake",
            inbound_bus=MagicMock(),
            inbound_audio_bus=MagicMock(),
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
        assert msg.routing.scope_id == "chat:123:topic:456:user:tg:user:42"
        assert msg.routing.thread_id == "456"

    def test_routing_platform_meta_is_copy(self) -> None:
        """RoutingContext.platform_meta must not alias InboundMessage.platform_meta."""
        from lyra.adapters.telegram import _ALLOW_ALL, TelegramAdapter

        adapter = TelegramAdapter(bot_id="main", token="fake", inbound_bus=MagicMock(), inbound_audio_bus=MagicMock(), auth=_ALLOW_ALL)

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
        assert msg.routing.platform_meta is not msg.platform_meta
        assert msg.routing.platform_meta == msg.platform_meta


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
        assert msg.routing.scope_id == "channel:789:user:dc:user:42"
        assert msg.routing.thread_id is None
        assert msg.routing.reply_to_message_id == "999"


# ---------------------------------------------------------------------------
# OutboundDispatcher._verify_routing — direct unit tests
# ---------------------------------------------------------------------------


class TestVerifyRoutingDirect:
    """Direct unit tests for _verify_routing (no async queue needed)."""

    def test_none_passes(self) -> None:
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

        d = OutboundDispatcher(
            platform_name="telegram", adapter=MagicMock(), bot_id="main"
        )
        assert d._verify_routing(None) is True

    def test_matching_passes(self) -> None:
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

        d = OutboundDispatcher(
            platform_name="telegram", adapter=MagicMock(), bot_id="main"
        )
        assert d._verify_routing(_RC_TG) is True

    def test_platform_mismatch_fails(self) -> None:
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

        d = OutboundDispatcher(
            platform_name="telegram", adapter=MagicMock(), bot_id="main"
        )
        assert d._verify_routing(_RC_DC) is False

    def test_bot_id_mismatch_fails(self) -> None:
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

        rc = RoutingContext(platform="telegram", bot_id="other", scope_id="chat:123")
        d = OutboundDispatcher(
            platform_name="telegram", adapter=MagicMock(), bot_id="main"
        )
        assert d._verify_routing(rc) is False


# ---------------------------------------------------------------------------
# OutboundDispatcher integration (queue-based, deterministic drain)
# ---------------------------------------------------------------------------


class TestDispatcherRoutingIntegration:
    async def test_matching_routing_passes(self) -> None:
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

        adapter = MagicMock()
        adapter.send = AsyncMock()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            msg = make_routing_inbound(routing=_RC_TG)
            outbound = OutboundMessage.from_text("hi")
            outbound.routing = _RC_TG
            dispatcher.enqueue(msg, outbound)
            await dispatcher._queue.join()
            adapter.send.assert_awaited_once()
        finally:
            await dispatcher.stop()

    async def test_mismatched_platform_drops(self) -> None:
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

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
            msg = make_routing_inbound(routing=rc)
            outbound = OutboundMessage.from_text("hi")
            outbound.routing = rc
            dispatcher.enqueue(msg, outbound)
            await dispatcher._queue.join()
            adapter.send.assert_not_awaited()
        finally:
            await dispatcher.stop()

    async def test_mismatched_bot_id_drops(self) -> None:
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

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
            msg = make_routing_inbound(routing=rc)
            outbound = OutboundMessage.from_text("hi")
            outbound.routing = rc
            dispatcher.enqueue(msg, outbound)
            await dispatcher._queue.join()
            adapter.send.assert_not_awaited()
        finally:
            await dispatcher.stop()

    async def test_none_routing_passes(self) -> None:
        """Backward compat: no routing context → message is delivered."""
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

        adapter = MagicMock()
        adapter.send = AsyncMock()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            msg = make_routing_inbound()  # routing=None
            outbound = OutboundMessage.from_text("hi")
            dispatcher.enqueue(msg, outbound)
            await dispatcher._queue.join()
            adapter.send.assert_awaited_once()
        finally:
            await dispatcher.stop()

    async def test_streaming_mismatched_routing_drops_and_drains(self) -> None:
        """Streaming with mismatched routing: message dropped, iterator drained."""
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

        adapter = MagicMock()
        adapter.send_streaming = AsyncMock()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            rc = RoutingContext(
                platform="discord", bot_id="main", scope_id="channel:456"
            )
            msg = make_routing_inbound(routing=rc)
            outbound = OutboundMessage.from_text("")
            outbound.routing = rc
            drained = False

            async def bad_chunks() -> AsyncIterator[TextRenderEvent]:
                nonlocal drained
                yield TextRenderEvent(text="chunk1", is_final=False)
                yield TextRenderEvent(text="chunk2", is_final=True)
                drained = True

            dispatcher.enqueue_streaming(msg, bad_chunks(), outbound)
            await dispatcher._queue.join()
            adapter.send_streaming.assert_not_awaited()
            assert drained, "iterator must be drained on routing mismatch"
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

        msg = make_routing_inbound(routing=_RC_TG)
        response = Response(content="hi")
        await hub.dispatch_response(msg, response)

        call_args = adapter.send.call_args
        outbound = call_args[0][1]
        assert outbound.routing is _RC_TG

    async def test_dispatch_response_does_not_overwrite_existing(self) -> None:
        """When outbound already has routing, hub must not overwrite it."""
        from lyra.core.hub import Hub

        hub = Hub()
        adapter = MagicMock()
        adapter.send = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)

        rc_outbound = RoutingContext(
            platform="telegram", bot_id="main", scope_id="chat:999"
        )
        msg = make_routing_inbound(routing=_RC_TG)
        outbound = OutboundMessage.from_text("hi")
        outbound.routing = rc_outbound
        await hub.dispatch_response(msg, outbound)

        call_args = adapter.send.call_args
        sent_outbound = call_args[0][1]
        assert sent_outbound.routing is rc_outbound

    async def test_dispatch_streaming_propagates_routing(self) -> None:
        from lyra.core.hub import Hub

        hub = Hub()
        adapter = MagicMock()
        adapter.send_streaming = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)

        msg = make_routing_inbound(routing=_RC_TG)
        outbound = OutboundMessage.from_text("")

        async def chunks() -> AsyncIterator[TextRenderEvent]:
            yield TextRenderEvent(text="hello", is_final=True)

        await hub.dispatch_streaming(msg, chunks(), outbound)
        assert outbound.routing is _RC_TG
