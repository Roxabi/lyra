"""Tests for Hub dispatch methods: response, attachment, audio, audio_stream, voice_stream."""  # noqa: E501

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core import Hub, Response
from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
)
from tests.core.conftest import make_inbound_message

# ---------------------------------------------------------------------------
# T5 — dispatch_response
# ---------------------------------------------------------------------------


class TestDispatchResponse:
    async def test_dispatches_to_correct_adapter(self) -> None:
        hub = Hub()
        sent: list[tuple[InboundMessage, OutboundMessage]] = []

        class CapturingAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                sent.append((original_msg, outbound))

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main")
        response = Response(content="pong")
        await hub.dispatch_response(msg, response)
        assert len(sent) == 1
        # dispatch_response converts Response → OutboundMessage; text is preserved
        assert "pong" in str(sent[0][1].content)

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        with pytest.raises(KeyError):
            await hub.dispatch_response(msg, Response(content="x"))

    async def test_updates_last_processed_at_on_success(self) -> None:
        """dispatch_response sets _last_processed_at on successful send."""
        hub = Hub()

        class DummyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", DummyAdapter())  # type: ignore[arg-type]
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")
        await hub.dispatch_response(msg, Response(content="ok"))
        assert hub._last_processed_at is not None

    async def test_no_update_last_processed_at_on_missing_adapter(self) -> None:
        """dispatch_response does NOT update _last_processed_at on KeyError."""
        hub = Hub()
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        with pytest.raises(KeyError):
            await hub.dispatch_response(msg, Response(content="x"))
        assert hub._last_processed_at is None


# ---------------------------------------------------------------------------
# #217 — dispatch_attachment
# ---------------------------------------------------------------------------


class TestDispatchAttachment:
    async def test_dispatches_to_correct_adapter(self) -> None:
        hub = Hub()
        sent: list[tuple[OutboundAttachment, InboundMessage]] = []

        class CapturingAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self, original_msg: InboundMessage, chunks: object, outbound=None
            ) -> None:
                pass

            async def render_attachment(
                self, attachment: OutboundAttachment, inbound: InboundMessage
            ) -> None:
                sent.append((attachment, inbound))

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main")
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        await hub.dispatch_attachment(msg, attachment)
        assert len(sent) == 1
        assert sent[0][0] is attachment

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        with pytest.raises(KeyError):
            await hub.dispatch_attachment(msg, attachment)

    async def test_updates_last_processed_at_on_success(self) -> None:
        hub = Hub()

        class DummyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self, original_msg: InboundMessage, chunks: object, outbound=None
            ) -> None:
                pass

            async def render_attachment(
                self, attachment: OutboundAttachment, inbound: InboundMessage
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", DummyAdapter())  # type: ignore[arg-type]
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        await hub.dispatch_attachment(msg, attachment)
        assert hub._last_processed_at is not None


# ---------------------------------------------------------------------------
# #182 — dispatch_audio
# ---------------------------------------------------------------------------


class TestDispatchAudio:
    async def test_routes_to_dispatcher(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        dispatcher = MagicMock()
        dispatcher.enqueue_audio = MagicMock()
        hub.register_outbound_dispatcher(Platform.TELEGRAM, "main", dispatcher)
        msg = make_inbound_message(platform="telegram", bot_id="main")
        audio = OutboundAudio(audio_bytes=b"ogg", mime_type="audio/ogg")
        await hub.dispatch_audio(msg, audio)
        dispatcher.enqueue_audio.assert_called_once_with(msg, audio)
        assert hub._last_processed_at is not None

    async def test_fallback_to_adapter(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        adapter.render_audio = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        msg = make_inbound_message(platform="telegram", bot_id="main")
        audio = OutboundAudio(audio_bytes=b"ogg", mime_type="audio/ogg")
        await hub.dispatch_audio(msg, audio)
        adapter.render_audio.assert_awaited_once_with(audio, msg)

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        audio = OutboundAudio(audio_bytes=b"ogg", mime_type="audio/ogg")
        with pytest.raises(KeyError):
            await hub.dispatch_audio(msg, audio)

    async def test_updates_last_processed_at_on_success(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        adapter.render_audio = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")
        audio = OutboundAudio(audio_bytes=b"ogg", mime_type="audio/ogg")
        await hub.dispatch_audio(msg, audio)
        assert hub._last_processed_at is not None


# ---------------------------------------------------------------------------
# #182 — dispatch_audio_stream
# ---------------------------------------------------------------------------


class TestDispatchAudioStream:
    async def test_routes_to_dispatcher(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        dispatcher = MagicMock()
        dispatcher.enqueue_audio_stream = MagicMock()
        hub.register_outbound_dispatcher(Platform.TELEGRAM, "main", dispatcher)
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        c = chunks()
        await hub.dispatch_audio_stream(msg, c)
        dispatcher.enqueue_audio_stream.assert_called_once_with(msg, c)
        assert hub._last_processed_at is not None

    async def test_fallback_to_adapter(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        adapter.render_audio_stream = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        c = chunks()
        await hub.dispatch_audio_stream(msg, c)
        adapter.render_audio_stream.assert_awaited_once()
        call_args = adapter.render_audio_stream.call_args[0]
        assert call_args[0] is c
        assert call_args[1] is msg

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        with pytest.raises(KeyError):
            await hub.dispatch_audio_stream(msg, chunks())

    async def test_updates_last_processed_at_on_success(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        adapter.render_audio_stream = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        await hub.dispatch_audio_stream(msg, chunks())
        assert hub._last_processed_at is not None


# ---------------------------------------------------------------------------
# #256 — dispatch_voice_stream
# ---------------------------------------------------------------------------


class TestDispatchVoiceStream:
    async def test_dispatch_voice_stream_routes_to_dispatcher(self) -> None:
        # Arrange
        hub = Hub()
        adapter = MagicMock()
        hub.register_adapter(Platform.DISCORD, "main", adapter)
        dispatcher = MagicMock()
        dispatcher.enqueue_voice_stream = MagicMock()
        hub.register_outbound_dispatcher(Platform.DISCORD, "main", dispatcher)
        msg = make_inbound_message(platform="discord", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"pcm", session_id="s1", chunk_index=0, is_final=True
            )

        c = chunks()
        await hub.dispatch_voice_stream(msg, c)
        dispatcher.enqueue_voice_stream.assert_called_once_with(msg, c)
        assert hub._last_processed_at is not None

    async def test_dispatch_voice_stream_fallback_direct(self) -> None:
        # Arrange — no dispatcher, adapter registered
        hub = Hub()
        adapter = MagicMock()
        adapter.render_voice_stream = AsyncMock()
        hub.register_adapter(Platform.DISCORD, "main", adapter)
        msg = make_inbound_message(platform="discord", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"pcm", session_id="s1", chunk_index=0, is_final=True
            )

        c = chunks()
        await hub.dispatch_voice_stream(msg, c)
        adapter.render_voice_stream.assert_awaited_once()
        call_args = adapter.render_voice_stream.call_args[0]
        # chunks first, then msg (same convention as dispatch_audio_stream)
        assert call_args[0] is c
        assert call_args[1] is msg

    async def test_dispatch_voice_stream_raises_if_no_adapter(self) -> None:
        # Arrange — no dispatcher, no adapter
        hub = Hub()
        msg = make_inbound_message(platform="discord", bot_id="ghost")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"pcm", session_id="s1", chunk_index=0, is_final=True
            )

        with pytest.raises(KeyError):
            await hub.dispatch_voice_stream(msg, chunks())


# ---------------------------------------------------------------------------
# RED — #138: OutboundMessage dispatch (Slice V2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_response_accepts_outbound_message() -> None:
    """hub.dispatch_response() must accept an OutboundMessage and forward it
    to the adapter's send() method unchanged (issue #138, Slice V2)."""
    hub = Hub()
    received: list[OutboundMessage] = []

    class MockAdapterV2:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            received.append(outbound)

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks: object,
            outbound=None,
        ) -> None:
            pass

    hub.register_adapter(Platform.TELEGRAM, "main", MockAdapterV2())  # type: ignore[arg-type]
    msg = make_inbound_message(platform="telegram", bot_id="main")
    outbound = OutboundMessage.from_text("hi")

    await hub.dispatch_response(msg, outbound)

    assert len(received) == 1
    assert received[0].content == ["hi"]


@pytest.mark.asyncio
async def test_dispatch_response_accepts_legacy_response() -> None:
    """hub.dispatch_response() must still accept a plain Response for backward
    compatibility — no call-site changes required at pool.py (issue #138, U5)."""
    hub = Hub()
    received: list[Response] = []

    class LegacyCapturingAdapter:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            received.append(outbound)  # type: ignore[arg-type]

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks: object,
            outbound=None,
        ) -> None:
            pass

    hub.register_adapter(Platform.TELEGRAM, "main", LegacyCapturingAdapter())  # type: ignore[arg-type]
    msg = make_inbound_message(platform="telegram", bot_id="main")

    await hub.dispatch_response(msg, Response(content="hi"))

    assert len(received) == 1
    result = received[0]
    content = result.content if isinstance(result, Response) else result.content  # type: ignore[union-attr]
    assert "hi" in str(content)
