"""Hub dispatch_audio, dispatch_audio_stream, dispatch_voice_stream."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core import Hub
from lyra.core.messaging.message import (
    OutboundAudio,
    OutboundAudioChunk,
    Platform,
)
from tests.core.conftest import make_inbound_message

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
