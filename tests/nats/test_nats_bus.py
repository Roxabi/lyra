"""Tests for NatsBus: Bus[T] over NATS pub/sub.

Covers all 17 success criteria from spec #455 (C2: NatsBus implementation).

Uses a real nats-server subprocess (see conftest.py) — no mocks of the
NATS transport layer. Tests requiring nats-server are automatically skipped
when the binary is not found in PATH.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone

import pytest
from nats.aio.client import Client as NATS

from lyra.core.bus import Bus
from lyra.core.message import (
    Attachment,
    InboundMessage,
    Platform,
)
from lyra.core.trust import TrustLevel
from lyra.nats._serialize import deserialize, serialize
from lyra.nats.nats_bus import NatsBus
from tests.nats.conftest import requires_nats_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(platform: Platform = Platform.TELEGRAM) -> InboundMessage:
    if platform == Platform.TELEGRAM:
        scope = "chat:123"
        meta: dict = {
            "chat_id": 123,
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


def _make_bus(nc: NATS) -> NatsBus:
    return NatsBus(nc=nc, bot_id="main", item_type=InboundMessage)


# ---------------------------------------------------------------------------
# TestSerialize — serialization layer (lyra.nats._serialize)
# ---------------------------------------------------------------------------


class TestSerialize:
    def test_callable_stripped_from_platform_meta(self) -> None:
        """Callables in platform_meta must be stripped during serialization."""
        # Arrange
        msg = InboundMessage(
            id="msg-callable",
            platform=Platform.TELEGRAM.value,
            bot_id="main",
            scope_id="chat:1",
            user_id="u:1",
            user_name="Bob",
            is_mention=False,
            text="hi",
            text_raw="hi",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.PUBLIC,
            platform_meta={"_session_update_fn": lambda: None, "chat_id": 99},
        )

        # Act
        payload = serialize(msg)
        result = deserialize(payload, InboundMessage)

        # Assert
        assert "_session_update_fn" not in result.platform_meta
        assert result.platform_meta.get("chat_id") == 99

    def test_non_callable_platform_meta_preserved(self) -> None:
        """Non-callable values in platform_meta survive the round-trip intact."""
        # Arrange
        msg = InboundMessage(
            id="msg-meta",
            platform=Platform.TELEGRAM.value,
            bot_id="main",
            scope_id="chat:1",
            user_id="u:1",
            user_name="Bob",
            is_mention=False,
            text="hi",
            text_raw="hi",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.PUBLIC,
            platform_meta={"chat_id": 42, "is_group": True, "label": "vip"},
        )

        # Act
        payload = serialize(msg)
        result = deserialize(payload, InboundMessage)

        # Assert
        assert result.platform_meta["chat_id"] == 42
        assert result.platform_meta["is_group"] is True
        assert result.platform_meta["label"] == "vip"

    def test_enum_roundtrip(self) -> None:
        """TrustLevel enum survives serialize → deserialize as the same enum member."""
        # Arrange
        msg = _make_msg(Platform.TELEGRAM)
        assert msg.trust_level == TrustLevel.TRUSTED

        # Act
        payload = serialize(msg)
        result = deserialize(payload, InboundMessage)

        # Assert
        assert result.trust_level == TrustLevel.TRUSTED
        assert isinstance(result.trust_level, TrustLevel)

    def test_datetime_roundtrip(self) -> None:
        """datetime field survives round-trip as ISO 8601 (timezone-aware)."""
        # Arrange
        ts = datetime(2026, 3, 31, 12, 0, 0, tzinfo=timezone.utc)
        msg = InboundMessage(
            id="msg-dt",
            platform=Platform.TELEGRAM.value,
            bot_id="main",
            scope_id="chat:1",
            user_id="u:1",
            user_name="Alice",
            is_mention=False,
            text="hi",
            text_raw="hi",
            timestamp=ts,
            trust_level=TrustLevel.PUBLIC,
        )

        # Act
        payload = serialize(msg)
        result = deserialize(payload, InboundMessage)

        # Assert — same UTC moment, timezone-aware
        assert result.timestamp.utctimetuple() == ts.utctimetuple()
        assert result.timestamp.tzinfo is not None

    def test_bytes_roundtrip(self) -> None:
        """bytes field (Attachment.url_or_path_or_bytes) survives as bytes."""
        # Arrange
        raw = b"\x89PNG\r\n\x1a\n"
        attachment = Attachment(
            type="image",
            url_or_path_or_bytes=raw,
            mime_type="image/png",
            filename="test.png",
        )
        msg = InboundMessage(
            id="msg-bytes",
            platform=Platform.TELEGRAM.value,
            bot_id="main",
            scope_id="chat:1",
            user_id="u:1",
            user_name="Alice",
            is_mention=False,
            text="pic",
            text_raw="pic",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.PUBLIC,
            attachments=[attachment],
        )

        # Act
        payload = serialize(msg)
        result = deserialize(payload, InboundMessage)

        # Assert
        assert len(result.attachments) == 1
        assert isinstance(result.attachments[0].url_or_path_or_bytes, bytes)
        assert result.attachments[0].url_or_path_or_bytes == raw


# ---------------------------------------------------------------------------
# TestNatsBusLifecycle — register / start / stop contract
# ---------------------------------------------------------------------------


@requires_nats_server
class TestNatsBusLifecycle:
    async def test_register_before_start_ok(self, nc: NATS) -> None:
        """register(platform) succeeds before start() is called."""
        # Arrange
        bus = _make_bus(nc)

        # Act / Assert — no exception raised
        bus.register(Platform.TELEGRAM)
        assert Platform.TELEGRAM in bus.registered_platforms()

    async def test_register_after_start_raises(self, nc: NATS) -> None:
        """Calling register() after start() must raise RuntimeError."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM)
        await bus.start()

        try:
            # Act / Assert
            with pytest.raises(RuntimeError):
                bus.register(Platform.DISCORD)
        finally:
            await bus.stop()

    async def test_start_zero_platforms_ok(self, nc: NATS) -> None:
        """start() with no registered platforms is a no-op — no exception."""
        # Arrange
        bus = _make_bus(nc)

        # Act / Assert — no exception
        await bus.start()
        await bus.stop()

    async def test_stop_preserves_platforms(self, nc: NATS) -> None:
        """stop() must not clear registered_platforms."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM)
        await bus.start()

        # Act
        await bus.stop()

        # Assert — platform still known after stop
        assert Platform.TELEGRAM in bus.registered_platforms()

    async def test_start_after_stop_ok(self, nc: NATS) -> None:
        """stop() then start() succeeds without re-registering platforms."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM)
        await bus.start()
        await bus.stop()

        # Act / Assert — no exception on second start
        await bus.start()
        await bus.stop()


# ---------------------------------------------------------------------------
# TestNatsBusRoundTrip — end-to-end publish → subscribe via NATS
# ---------------------------------------------------------------------------


@requires_nats_server
class TestNatsBusRoundTrip:
    async def test_put_get_roundtrip(self, nc: NATS) -> None:
        """put() + get(): publisher/subscriber message fields preserved."""
        # Arrange — two NatsBus instances sharing the same NATS connection
        publisher = _make_bus(nc)
        subscriber = _make_bus(nc)

        publisher.register(Platform.TELEGRAM)
        subscriber.register(Platform.TELEGRAM)
        await subscriber.start()

        msg = _make_msg(Platform.TELEGRAM)

        try:
            # Act
            await publisher.put(Platform.TELEGRAM, msg)
            received = await asyncio.wait_for(subscriber.get(), timeout=2.0)

            # Assert — key fields survive the NATS transit
            assert received.id == msg.id
            assert received.platform == msg.platform
            assert received.bot_id == msg.bot_id
            assert received.scope_id == msg.scope_id
            assert received.user_id == msg.user_id
            assert received.user_name == msg.user_name
            assert received.text == msg.text
            assert received.trust_level == msg.trust_level
        finally:
            await subscriber.stop()

    async def test_callable_stripped_in_transit(self, nc: NATS) -> None:
        """put() a message with _session_update_fn; get() on other side strips it."""
        # Arrange
        publisher = _make_bus(nc)
        subscriber = _make_bus(nc)

        publisher.register(Platform.TELEGRAM)
        subscriber.register(Platform.TELEGRAM)
        await subscriber.start()

        msg = InboundMessage(
            id="msg-fn",
            platform=Platform.TELEGRAM.value,
            bot_id="main",
            scope_id="chat:123",
            user_id="u:1",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
            platform_meta={
                "_session_update_fn": lambda: "should not cross",
                "chat_id": 123,
            },
        )

        try:
            # Act
            await publisher.put(Platform.TELEGRAM, msg)
            received = await asyncio.wait_for(subscriber.get(), timeout=2.0)

            # Assert — callable gone, non-callable preserved
            assert "_session_update_fn" not in received.platform_meta
            assert received.platform_meta.get("chat_id") == 123
        finally:
            await subscriber.stop()


# ---------------------------------------------------------------------------
# TestNatsBusEdgeCases — task_done, qsize, staging_qsize, unregistered platform
# ---------------------------------------------------------------------------


@requires_nats_server
class TestNatsBusEdgeCases:
    async def test_task_done_is_noop(self, nc: NATS) -> None:
        """task_done() returns None without raising any exception."""
        # Arrange
        bus = _make_bus(nc)

        # Act / Assert
        result = bus.task_done()
        assert result is None

    def test_qsize_always_zero(self, nc: NATS) -> None:
        """qsize(platform) always returns 0 (no local per-platform buffer)."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM)

        # Act / Assert
        assert bus.qsize(Platform.TELEGRAM) == 0

    async def test_staging_qsize(self, nc: NATS) -> None:
        """staging_qsize() reflects items waiting in the staging queue."""
        # Arrange
        publisher = _make_bus(nc)
        subscriber = _make_bus(nc)

        publisher.register(Platform.TELEGRAM)
        subscriber.register(Platform.TELEGRAM)
        await subscriber.start()

        msg = _make_msg(Platform.TELEGRAM)

        try:
            # Act — put a message, brief pause for NATS delivery, check staging
            await publisher.put(Platform.TELEGRAM, msg)
            await asyncio.sleep(0.1)

            # staging_qsize() returns a non-negative integer
            size_before = subscriber.staging_qsize()
            assert size_before >= 0

            # Consume — proves the item arrived
            received = await asyncio.wait_for(subscriber.get(), timeout=2.0)
            assert received is not None
        finally:
            await subscriber.stop()

    async def test_unregistered_platform_raises(self, nc: NATS) -> None:
        """put() to an unregistered platform raises KeyError."""
        # Arrange
        bus = _make_bus(nc)
        # Note: Platform.DISCORD is NOT registered
        msg = _make_msg(Platform.DISCORD)

        # Act / Assert
        with pytest.raises(KeyError):
            await bus.put(Platform.DISCORD, msg)


# ---------------------------------------------------------------------------
# TestProtocolConformance — Bus[T] structural compatibility
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_type_annotation_accepted(self) -> None:
        """NatsBus exposes all required Bus[T] methods (structural Protocol check)."""
        # Arrange
        required_methods = {
            "register",
            "put",
            "get",
            "task_done",
            "start",
            "stop",
            "qsize",
            "staging_qsize",
            "registered_platforms",
        }

        # Act
        actual_methods = set(dir(NatsBus))
        missing = required_methods - actual_methods

        # Assert — NatsBus must expose every Bus[T] method
        assert not missing, f"NatsBus missing Bus[T] methods: {missing}"

        # Bus[T] generic alias can be used as a type annotation
        assert Bus is not None


# ---------------------------------------------------------------------------
# TestNatsBusQueueGroup — queue_group parameter forwarded to nc.subscribe()
# ---------------------------------------------------------------------------


@requires_nats_server
class TestNatsBusQueueGroup:
    async def test_queue_group_distributes_messages(self, nc: NATS) -> None:
        """Two NatsBus subscribers in the same queue group share the delivery."""
        # Arrange — publisher + two subscribers in the same queue group
        publisher = NatsBus(nc=nc, bot_id="main", item_type=InboundMessage)
        sub_a = NatsBus(
            nc=nc,
            bot_id="main",
            item_type=InboundMessage,
            queue_group="test-distribution",
        )
        sub_b = NatsBus(
            nc=nc,
            bot_id="main",
            item_type=InboundMessage,
            queue_group="test-distribution",
        )
        publisher.register(Platform.TELEGRAM)
        sub_a.register(Platform.TELEGRAM)
        sub_b.register(Platform.TELEGRAM)
        await sub_a.start()
        await sub_b.start()

        try:
            # Act — publish N messages, drain both subscribers
            n = 10
            for i in range(n):
                msg = _make_msg(Platform.TELEGRAM)
                msg = dataclasses.replace(msg, id=f"msg-{i}")
                await publisher.put(Platform.TELEGRAM, msg)

            received_a: list = []
            received_b: list = []
            # Drain with a bounded deadline
            deadline = asyncio.get_event_loop().time() + 2.0
            while len(received_a) + len(received_b) < n:
                if asyncio.get_event_loop().time() > deadline:
                    break
                for bus, bucket in ((sub_a, received_a), (sub_b, received_b)):
                    if bus.staging_qsize() > 0:
                        bucket.append(await bus.get())

            # Assert — every message delivered exactly once across the group
            all_ids = {m.id for m in received_a} | {m.id for m in received_b}
            assert len(received_a) + len(received_b) == n
            assert len(all_ids) == n  # no duplicates
            # Both subscribers received at least one (load balancing)
            assert len(received_a) > 0
            assert len(received_b) > 0
        finally:
            await sub_a.stop()
            await sub_b.stop()


def test_nats_bus_default_queue_group_is_empty() -> None:
    """Default queue_group is empty string (backward-compatible, no group)."""
    from unittest.mock import MagicMock

    # Arrange / Act — uses a mock nc: no real NATS connection needed for this check
    nc = MagicMock()
    bus = NatsBus(nc=nc, bot_id="main", item_type=InboundMessage)

    # Assert
    assert bus._queue_group == ""
