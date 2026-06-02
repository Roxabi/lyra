"""KindRouter unit tests — RED phase for _resolve_item / RoutedPayload extraction.

These tests import `_resolve_item` and `RoutedPayload` from
`factory.core.hub.outbound._dispatch`, which do not yet exist.  They are
intended to drive the extraction of the kind-routing logic (lines 37-73 of
`_dispatch.py`) into a standalone, testable unit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock

from factory.core.hub.outbound._dispatch import RoutedPayload, _resolve_item
from factory.core.messaging.message import (
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    RoutingContext,
)
from tests.core.conftest import make_dispatcher_msg
from tests.helpers.messages import make_test_blobref

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_routing(
    platform: str = "telegram",
    bot_id: str = "main",
    scope_id: str = "chat:123",
) -> RoutingContext:
    return RoutingContext(platform=platform, bot_id=bot_id, scope_id=scope_id)


async def _async_iter(*items: object) -> AsyncIterator[object]:
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


class TestResolveItemSend:
    async def test_resolve_item_send_unpacks_payload_and_routing(self) -> None:
        """send: payload is OutboundMessage; routing taken from payload.routing."""
        # Arrange
        msg = make_dispatcher_msg()
        payload = OutboundMessage.from_text("hello")
        payload.routing = _make_routing()
        item = ("send", msg, payload)
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert isinstance(result, RoutedPayload)
        assert result.kind == "send"
        assert result.msg is msg
        assert result.payload is payload
        assert result.outbound is None
        assert result.routing is payload.routing
        verify.assert_called_once_with(payload.routing)

    async def test_resolve_item_send_falls_back_to_msg_routing(self) -> None:
        """send: when payload.routing is absent, fallback to msg.routing."""
        # Arrange
        msg = make_dispatcher_msg()
        object.__setattr__(msg, "routing", _make_routing())
        payload = OutboundMessage.from_text("hello")
        # payload.routing is None by default
        item = ("send", msg, payload)
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert isinstance(result, RoutedPayload)
        assert result.routing is msg.routing


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------


class TestResolveItemStreaming:
    async def test_resolve_item_streaming_unpacks_payload_and_outbound(self) -> None:
        """streaming: payload is AsyncIterator, outbound is OutboundMessage;
        routing taken from outbound.routing when present."""
        # Arrange
        msg = make_dispatcher_msg()
        outbound = OutboundMessage.from_text("stream")
        outbound.routing = _make_routing()
        payload = _async_iter()
        item = ("streaming", msg, payload, outbound)
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert isinstance(result, RoutedPayload)
        assert result.kind == "streaming"
        assert result.msg is msg
        assert result.payload is payload
        assert result.outbound is outbound
        assert result.routing is outbound.routing

    async def test_resolve_item_streaming_fallback_to_msg_routing(self) -> None:
        """streaming: when outbound is None, routing falls back to msg.routing."""
        # Arrange
        msg = make_dispatcher_msg()
        object.__setattr__(msg, "routing", _make_routing())
        payload = _async_iter()
        item = ("streaming", msg, payload, None)
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert isinstance(result, RoutedPayload)
        assert result.routing is msg.routing


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


class TestResolveItemAudio:
    async def test_resolve_item_audio_unpacks_and_routes_via_msg(self) -> None:
        """audio: payload is OutboundAudio; routing is msg.routing."""
        # Arrange
        msg = make_dispatcher_msg()
        object.__setattr__(msg, "routing", _make_routing())
        audio = OutboundAudio(blob_ref=make_test_blobref(b"ogg"), mime_type="audio/ogg")
        item = ("audio", msg, audio)
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert isinstance(result, RoutedPayload)
        assert result.kind == "audio"
        assert result.msg is msg
        assert result.payload is audio
        assert result.outbound is None
        assert result.routing is msg.routing


# ---------------------------------------------------------------------------
# audio_stream
# ---------------------------------------------------------------------------


class TestResolveItemAudioStream:
    async def test_resolve_item_audio_stream_unpacks_and_routes_via_msg(self) -> None:
        """audio_stream: payload is AsyncIterator; routing is msg.routing."""
        # Arrange
        msg = make_dispatcher_msg()
        object.__setattr__(msg, "routing", _make_routing())

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        payload = chunks()
        item = ("audio_stream", msg, payload)
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert isinstance(result, RoutedPayload)
        assert result.kind == "audio_stream"
        assert result.msg is msg
        assert result.payload is payload
        assert result.outbound is None
        assert result.routing is msg.routing


# ---------------------------------------------------------------------------
# voice_stream
# ---------------------------------------------------------------------------


class TestResolveItemVoiceStream:
    async def test_resolve_item_voice_stream_synthesizes_routing(self) -> None:
        """voice_stream: when msg.routing is absent, synthesize from msg fields."""
        # Arrange
        msg = make_dispatcher_msg()
        # msg.routing is None by default
        item = ("voice_stream", msg, _async_iter())
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert isinstance(result, RoutedPayload)
        assert result.kind == "voice_stream"
        assert result.routing == RoutingContext(
            platform=msg.platform,
            bot_id=msg.bot_id,
            scope_id=msg.scope_id,
        )

    async def test_resolve_item_voice_stream_prefers_msg_routing(self) -> None:
        """voice_stream: when msg.routing is present, use it directly."""
        # Arrange
        msg = make_dispatcher_msg()
        object.__setattr__(msg, "routing", _make_routing())
        item = ("voice_stream", msg, _async_iter())
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert isinstance(result, RoutedPayload)
        assert result.routing is msg.routing


# ---------------------------------------------------------------------------
# attachment
# ---------------------------------------------------------------------------


class TestResolveItemAttachment:
    async def test_resolve_item_attachment_unpacks_and_routes_via_msg(self) -> None:
        """attachment: payload is OutboundAttachment; routing is msg.routing."""
        # Arrange
        msg = make_dispatcher_msg()
        object.__setattr__(msg, "routing", _make_routing())
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        item = ("attachment", msg, attachment)
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert isinstance(result, RoutedPayload)
        assert result.kind == "attachment"
        assert result.msg is msg
        assert result.payload is attachment
        assert result.outbound is None
        assert result.routing is msg.routing


# ---------------------------------------------------------------------------
# Routing mismatch edge cases
# ---------------------------------------------------------------------------


class TestResolveItemRoutingMismatch:
    async def test_resolve_item_mismatch_returns_none_for_send(self) -> None:
        """Routing mismatch on send kind returns None immediately (no drain)."""
        # Arrange
        msg = make_dispatcher_msg()
        payload = OutboundMessage.from_text("hi")
        item = ("send", msg, payload)
        verify = MagicMock(return_value=False)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert result is None

    async def test_resolve_item_mismatch_drains_streaming(self) -> None:
        """Routing mismatch on streaming drains the async iterator."""
        # Arrange
        msg = make_dispatcher_msg()
        drained: list[int] = []

        async def chunks() -> AsyncIterator[str]:
            drained.append(1)
            yield "a"
            drained.append(2)
            yield "b"

        item = ("streaming", msg, chunks(), None)
        verify = MagicMock(return_value=False)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert result is None
        assert drained == [1, 2]

    async def test_resolve_item_mismatch_drains_audio_stream(self) -> None:
        """Routing mismatch on audio_stream drains the async iterator."""
        # Arrange
        msg = make_dispatcher_msg()
        drained: list[int] = []

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            drained.append(1)
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        item = ("audio_stream", msg, chunks())
        verify = MagicMock(return_value=False)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert result is None
        assert drained == [1]

    async def test_resolve_item_mismatch_drains_voice_stream(self) -> None:
        """Routing mismatch on voice_stream drains the async iterator."""
        # Arrange
        msg = make_dispatcher_msg()
        drained: list[int] = []

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            drained.append(1)
            yield OutboundAudioChunk(
                chunk_bytes=b"pcm", session_id="s1", chunk_index=0, is_final=True
            )

        item = ("voice_stream", msg, chunks())
        verify = MagicMock(return_value=False)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert result is None
        assert drained == [1]


# ---------------------------------------------------------------------------
# Unknown kind
# ---------------------------------------------------------------------------


class TestResolveItemUnknownKind:
    async def test_resolve_item_unknown_kind_returns_none(self) -> None:
        """Unknown kind logs an error and returns None."""
        # Arrange
        msg = make_dispatcher_msg()
        item = ("unknown", msg, "payload")
        verify = MagicMock(return_value=True)

        # Act
        result = await _resolve_item(item, verify)

        # Assert
        assert result is None
        verify.assert_not_called()
