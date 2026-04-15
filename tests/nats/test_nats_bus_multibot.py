"""Tests for NatsBus multi-bot support.

Covers (platform, bot_id) keying: multiple bot_ids on the same platform,
independent subjects, and backward-compat fallback to the constructor's bot_id.

Tests requiring nats-server are automatically skipped when the binary is not
found in PATH (same convention as test_nats_bus.py).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from nats.aio.client import Client as NATS

from lyra.core.message import InboundMessage, Platform
from lyra.core.trust import TrustLevel
from lyra.nats.nats_bus import NatsBus
from tests.nats.conftest import requires_nats_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(
    platform: Platform = Platform.TELEGRAM, bot_id: str = "main"
) -> InboundMessage:
    if platform == Platform.TELEGRAM:
        scope = "chat:1"
        meta: dict = {
            "chat_id": 1,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        }
    else:
        scope = "channel:2"
        meta = {
            "guild_id": 1,
            "channel_id": 2,
            "message_id": 3,
            "thread_id": None,
            "channel_type": "text",
        }
    return InboundMessage(
        id="msg-1",
        platform=platform.value,
        bot_id=bot_id,
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


def _make_bus(nc: NATS, bot_id: str = "main") -> NatsBus:
    return NatsBus(nc=nc, bot_id=bot_id, item_type=InboundMessage)


# ---------------------------------------------------------------------------
# TestMultiBotRegistration — (platform, bot_id) keying
# ---------------------------------------------------------------------------


@requires_nats_server
class TestMultiBotRegistration:
    async def test_two_bot_ids_same_platform_both_subscribed(self, nc: NATS) -> None:
        """Registering two bot_ids on the same platform creates two subscriptions."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM, bot_id="bot-a")
        bus.register(Platform.TELEGRAM, bot_id="bot-b")

        await bus.start()
        try:
            # Assert — two distinct subscriptions keyed by (platform, bot_id)
            assert (Platform.TELEGRAM, "bot-a") in bus._subscriptions
            assert (Platform.TELEGRAM, "bot-b") in bus._subscriptions
            assert len(bus._subscriptions) == 2
        finally:
            await bus.stop()

    async def test_messages_on_separate_subjects_both_arrive_in_staging(
        self, nc: NATS
    ) -> None:
        """Messages to each (platform, bot_id) subject both land in staging."""
        # Arrange
        _publisher = _make_bus(nc)
        subscriber = _make_bus(nc)

        subscriber.register(Platform.TELEGRAM, bot_id="bot-a")
        subscriber.register(Platform.TELEGRAM, bot_id="bot-b")
        await subscriber.start()

        msg_a = _make_msg(Platform.TELEGRAM, bot_id="bot-a")
        msg_b = _make_msg(Platform.TELEGRAM, bot_id="bot-b")

        # We need a publisher that knows about each bot_id; publish directly via nc
        # to bypass NatsBus.put() which only uses the first matching registration.
        import lyra.nats._serialize as _s

        try:
            # Act — publish to both subjects independently
            await nc.publish(
                f"lyra.inbound.{Platform.TELEGRAM.value}.bot-a", _s.serialize(msg_a)
            )
            await nc.publish(
                f"lyra.inbound.{Platform.TELEGRAM.value}.bot-b", _s.serialize(msg_b)
            )

            # Allow NATS delivery
            await asyncio.sleep(0.15)

            # Assert — staging queue received both messages
            assert subscriber.staging_qsize() == 2

            received_ids = set()
            for _ in range(2):
                item = await asyncio.wait_for(subscriber.get(), timeout=2.0)
                received_ids.add(item.bot_id)

            assert "bot-a" in received_ids
            assert "bot-b" in received_ids
        finally:
            await subscriber.stop()

    async def test_stop_clears_all_multibot_subscriptions(self, nc: NATS) -> None:
        """stop() clears all subscriptions, including multi-bot ones."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM, bot_id="bot-a")
        bus.register(Platform.TELEGRAM, bot_id="bot-b")
        await bus.start()

        assert len(bus._subscriptions) == 2

        # Act
        await bus.stop()

        # Assert
        assert len(bus._subscriptions) == 0

    async def test_restart_after_stop_resubscribes_all(self, nc: NATS) -> None:
        """stop() then start() re-creates all (platform, bot_id) subscriptions."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM, bot_id="bot-a")
        bus.register(Platform.TELEGRAM, bot_id="bot-b")
        await bus.start()
        await bus.stop()

        # Act
        await bus.start()
        try:
            assert (Platform.TELEGRAM, "bot-a") in bus._subscriptions
            assert (Platform.TELEGRAM, "bot-b") in bus._subscriptions
        finally:
            await bus.stop()

    async def test_registered_platforms_deduplicates(self, nc: NATS) -> None:
        """registered_platforms() returns each Platform once with multiple bot_ids."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM, bot_id="bot-a")
        bus.register(Platform.TELEGRAM, bot_id="bot-b")
        bus.register(Platform.DISCORD, bot_id="bot-a")

        # Assert — only two distinct Platform values
        platforms = bus.registered_platforms()
        assert platforms == frozenset({Platform.TELEGRAM, Platform.DISCORD})


# ---------------------------------------------------------------------------
# TestMultiBotBackwardCompat — fallback to constructor bot_id
# ---------------------------------------------------------------------------


@requires_nats_server
class TestMultiBotBackwardCompat:
    async def test_register_without_bot_id_uses_constructor_bot_id(
        self, nc: NATS
    ) -> None:
        """register(platform) without bot_id falls back to constructor's bot_id."""
        # Arrange
        bus = _make_bus(nc, bot_id="main")
        bus.register(Platform.TELEGRAM)  # no bot_id argument

        await bus.start()
        try:
            # Assert — subscription keyed as (TELEGRAM, "main")
            assert (Platform.TELEGRAM, "main") in bus._subscriptions
        finally:
            await bus.stop()

    async def test_put_without_explicit_bot_id_uses_first_registration(
        self, nc: NATS
    ) -> None:
        """put() routes to the first matching registration's bot_id."""
        # Arrange
        publisher = _make_bus(nc, bot_id="main")
        publisher.register(Platform.TELEGRAM)  # falls back to "main"

        subscriber = _make_bus(nc, bot_id="main")
        subscriber.register(Platform.TELEGRAM)  # falls back to "main"
        await subscriber.start()

        msg = _make_msg(Platform.TELEGRAM)

        try:
            # Act — put() should publish to lyra.inbound.telegram.main
            await publisher.put(Platform.TELEGRAM, msg)
            received = await asyncio.wait_for(subscriber.get(), timeout=2.0)

            # Assert
            assert received.id == msg.id
            assert received.platform == msg.platform
        finally:
            await subscriber.stop()

    def test_register_without_bot_id_in_registrations(self, nc: NATS) -> None:
        """register(platform) without bot_id stores (platform, constructor_bot_id)."""
        # Arrange
        bus = _make_bus(nc, bot_id="my-bot")
        bus.register(Platform.TELEGRAM)

        # Assert — registration is (TELEGRAM, "my-bot"), not (TELEGRAM, None)
        assert (Platform.TELEGRAM, "my-bot") in bus._registrations

    async def test_mix_explicit_and_implicit_bot_ids(self, nc: NATS) -> None:
        """register() with and without bot_id can coexist."""
        # Arrange
        bus = _make_bus(nc, bot_id="default")
        bus.register(Platform.TELEGRAM)  # uses "default"
        bus.register(Platform.TELEGRAM, bot_id="alt")  # explicit

        await bus.start()
        try:
            assert (Platform.TELEGRAM, "default") in bus._subscriptions
            assert (Platform.TELEGRAM, "alt") in bus._subscriptions
            assert len(bus._subscriptions) == 2
        finally:
            await bus.stop()


# ---------------------------------------------------------------------------
# Publish-only adapter bus roundtrip
# ---------------------------------------------------------------------------


@requires_nats_server
async def test_publish_only_adapter_bus_roundtrip(nc: NATS) -> None:
    """Adapter publish-only bus publishes; hub-side normal bus receives.

    Mirrors the production split: adapter side opens no subscriptions,
    hub side subscribes and consumes via get().
    """
    # Arrange — adapter-side bus (publish_only=True)
    adapter_bus = NatsBus(
        nc=nc, bot_id="bot-a", item_type=InboundMessage, publish_only=True
    )
    adapter_bus.register(Platform.TELEGRAM, bot_id="bot-a")

    # Arrange — hub-side bus (normal mode)
    hub_bus = NatsBus(nc=nc, bot_id="bot-a", item_type=InboundMessage)
    hub_bus.register(Platform.TELEGRAM, bot_id="bot-a")

    try:
        # Act — start both buses
        await adapter_bus.start()
        await hub_bus.start()

        # Assert — adapter-side opened zero subscriptions
        assert adapter_bus.subscription_count == 0

        # Act — publish via adapter put()
        msg = _make_msg(Platform.TELEGRAM, bot_id="bot-a")
        await adapter_bus.put(Platform.TELEGRAM, msg)

        # Act — consume on hub side
        received = await asyncio.wait_for(hub_bus.get(), timeout=2.0)

        # Assert — full-field equality (stronger than id-only). Matches
        # the field list used by test_put_get_roundtrip in test_nats_bus.py.
        assert received.id == msg.id
        assert received.platform == msg.platform
        assert received.bot_id == msg.bot_id
        assert received.scope_id == msg.scope_id
        assert received.user_id == msg.user_id
        assert received.user_name == msg.user_name
        assert received.text == msg.text
        assert received.trust_level == msg.trust_level

        # Assert — adapter bus still has zero subscriptions after put()
        # (staging_qsize is tautologically 0 on publish-only; subscription_count
        # is the meaningful invariant here)
        assert adapter_bus.subscription_count == 0
    finally:
        await adapter_bus.stop()
        await hub_bus.stop()
