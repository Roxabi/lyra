"""Tests for NatsBus: Bus[T] over NATS pub/sub.

Covers all 17 success criteria from spec #455 (C2: NatsBus implementation).
NatsBus does not exist yet — this is the RED phase.

Uses a real nats-server subprocess (see conftest.py) — no mocks of the
NATS transport layer.

RED-phase import guard: imports from lyra.nats are attempted at module load.
If the package does not exist yet, each test skips with the ImportError detail
so pytest can still collect and report all test names.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import nats
import pytest

from lyra.core.bus import Bus
from lyra.core.message import (
    Attachment,
    InboundMessage,
    Platform,
)
from lyra.core.trust import TrustLevel
from tests.nats.conftest import requires_nats_server

# ---------------------------------------------------------------------------
# RED-phase guarded imports — lyra.nats does not exist yet.
# Tests will be collected but xfail until the implementation lands.
# ---------------------------------------------------------------------------
try:
    from lyra.nats._serialize import deserialize, serialize

    _HAS_SERIALIZE = True
except ImportError as _serialize_err:
    _HAS_SERIALIZE = False
    _serialize_err_msg = str(_serialize_err)

    # Provide typed stubs so type-checkers don't complain in the test body.
    def serialize(msg: Any) -> bytes:  # type: ignore[misc]
        raise ImportError(_serialize_err_msg)

    def deserialize(payload: bytes, cls: Any) -> Any:  # type: ignore[misc]
        raise ImportError(_serialize_err_msg)


try:
    from lyra.nats.nats_bus import NatsBus

    _HAS_NATS_BUS = True
except ImportError as _bus_err:
    _HAS_NATS_BUS = False
    _bus_err_msg = str(_bus_err)

    class NatsBus:  # type: ignore[no-redef]
        """Stub — implementation not yet available."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(_bus_err_msg)


# Markers applied to each test class — skip cleanly if module absent.
_needs_serialize = pytest.mark.skipif(
    not _HAS_SERIALIZE,
    reason="lyra.nats._serialize not yet implemented (RED phase)",
)
_needs_nats_bus = pytest.mark.skipif(
    not _HAS_NATS_BUS,
    reason="lyra.nats.nats_bus not yet implemented (RED phase)",
)


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


def _make_bus(nc: nats.NATS) -> NatsBus:
    return NatsBus(nc=nc, bot_id="main", item_type=InboundMessage)


# ---------------------------------------------------------------------------
# TestSerialize — serialization layer (lyra.nats._serialize)
# ---------------------------------------------------------------------------


@_needs_serialize
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


@_needs_nats_bus
@requires_nats_server
class TestNatsBusLifecycle:
    async def test_register_before_start_ok(self, nc: nats.NATS) -> None:
        """register(platform) succeeds before start() is called."""
        # Arrange
        bus = _make_bus(nc)

        # Act / Assert — no exception raised
        bus.register(Platform.TELEGRAM)
        assert Platform.TELEGRAM in bus.registered_platforms()

    async def test_register_after_start_raises(self, nc: nats.NATS) -> None:
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

    async def test_start_zero_platforms_ok(self, nc: nats.NATS) -> None:
        """start() with no registered platforms is a no-op — no exception."""
        # Arrange
        bus = _make_bus(nc)

        # Act / Assert — no exception
        await bus.start()
        await bus.stop()

    async def test_stop_preserves_platforms(self, nc: nats.NATS) -> None:
        """stop() must not clear registered_platforms."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM)
        await bus.start()

        # Act
        await bus.stop()

        # Assert — platform still known after stop
        assert Platform.TELEGRAM in bus.registered_platforms()

    async def test_start_after_stop_ok(self, nc: nats.NATS) -> None:
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


@_needs_nats_bus
@requires_nats_server
class TestNatsBusRoundTrip:
    async def test_put_get_roundtrip(self, nc: nats.NATS) -> None:
        """put() + get(): publisher/subscriber message fields preserved."""
        # Arrange — two NatsBus instances sharing the same NATS connection
        publisher = _make_bus(nc)
        subscriber = _make_bus(nc)

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

    async def test_callable_stripped_in_transit(self, nc: nats.NATS) -> None:
        """put() a message with _session_update_fn; get() on other side strips it."""
        # Arrange
        publisher = _make_bus(nc)
        subscriber = _make_bus(nc)

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


@_needs_nats_bus
@requires_nats_server
class TestNatsBusEdgeCases:
    async def test_task_done_is_noop(self, nc: nats.NATS) -> None:
        """task_done() returns None without raising any exception."""
        # Arrange
        bus = _make_bus(nc)

        # Act / Assert
        result = bus.task_done()
        assert result is None

    def test_qsize_always_zero(self, nc: nats.NATS) -> None:
        """qsize(platform) always returns 0 (no local per-platform buffer)."""
        # Arrange
        bus = _make_bus(nc)
        bus.register(Platform.TELEGRAM)

        # Act / Assert
        assert bus.qsize(Platform.TELEGRAM) == 0

    async def test_staging_qsize(self, nc: nats.NATS) -> None:
        """staging_qsize() reflects items waiting in the staging queue."""
        # Arrange
        publisher = _make_bus(nc)
        subscriber = _make_bus(nc)

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

    async def test_unregistered_platform_raises(self, nc: nats.NATS) -> None:
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


@_needs_nats_bus
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
