"""Tests for discord_voice.py — VoiceSessionManager.stream() and
DiscordAdapter.render_voice_stream() (issue #256)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.discord_voice import (
    PCMQueueSource,
    VoiceMode,
    VoiceSession,
    VoiceSessionManager,
)
from lyra.core.message import InboundMessage, OutboundAudioChunk
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# File-local helpers
# ---------------------------------------------------------------------------


def _make_vsm() -> VoiceSessionManager:
    return VoiceSessionManager()


def _make_chunk(is_final: bool = False) -> OutboundAudioChunk:
    return OutboundAudioChunk(
        chunk_bytes=b"pcm-data",
        session_id="s1",
        chunk_index=0,
        is_final=is_final,
    )


def _make_voice_session(
    guild_id: str = "1",
    mode: VoiceMode = VoiceMode.TRANSIENT,
    is_playing: bool = False,
) -> tuple[VoiceSession, MagicMock, MagicMock]:
    """Return (session, voice_client_mock, source) for stream tests."""
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = is_playing
    vc.disconnect = AsyncMock()
    source = MagicMock(spec=PCMQueueSource)
    session = VoiceSession(
        guild_id=guild_id,
        voice_client=vc,
        channel_id=100,
        mode=mode,
        source=source,
    )
    return session, vc, source


def _make_discord_inbound(
    platform_meta: dict | None = None,
) -> InboundMessage:
    """Build a minimal discord InboundMessage for render_voice_stream tests."""
    meta = (
        platform_meta
        if platform_meta is not None
        else {
            "guild_id": "1",
            "channel_id": 333,
            "message_id": 555,
            "thread_id": None,
            "channel_type": "text",
        }
    )
    return InboundMessage(
        id="msg-discord",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text="hey",
        text_raw="hey",
        timestamp=datetime.now(timezone.utc),
        platform_meta=meta,
        trust_level=TrustLevel.TRUSTED,
    )


# ---------------------------------------------------------------------------
# #256 — VoiceSessionManager.stream()
# ---------------------------------------------------------------------------


class TestVSMStream:
    @pytest.mark.asyncio
    async def test_stream_calls_play_and_drains_chunks(self) -> None:
        # Arrange
        vsm = _make_vsm()
        session, vc, _ = _make_voice_session(is_playing=False)
        vsm._sessions["1"] = session
        new_source = MagicMock(spec=PCMQueueSource)

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk(is_final=False)
            yield _make_chunk(is_final=False)

        # Act — patch PCMQueueSource so stream() assigns the mock as the fresh source
        with patch(
            "lyra.adapters.discord_voice.PCMQueueSource", return_value=new_source
        ):
            await vsm.stream("1", chunks())

        # Assert
        vc.play.assert_called_once()
        assert new_source.push.call_count == 2
        new_source.push.assert_any_call(b"pcm-data")

    @pytest.mark.asyncio
    async def test_stream_no_session_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange
        vsm = _make_vsm()

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk()

        # Act
        with caplog.at_level(logging.WARNING):
            await vsm.stream("999", chunks())

        # Assert — play() was never called (no session) and WARNING was logged
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    @pytest.mark.asyncio
    async def test_stream_already_playing_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — is_playing() returns True
        vsm = _make_vsm()
        session, vc, _ = _make_voice_session(is_playing=True)
        vsm._sessions["1"] = session

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk()

        # Act
        with caplog.at_level(logging.WARNING):
            await vsm.stream("1", chunks())

        # Assert — play() was NOT called again, WARNING logged
        vc.play.assert_not_called()
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    @pytest.mark.asyncio
    async def test_stream_is_final_calls_push_eof(self) -> None:
        # Arrange
        vsm = _make_vsm()
        session, _, _ = _make_voice_session(is_playing=False)
        vsm._sessions["1"] = session
        new_source = MagicMock(spec=PCMQueueSource)

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk(is_final=False)
            yield _make_chunk(is_final=True)

        # Act — patch PCMQueueSource so stream() assigns the mock as the fresh source
        with patch(
            "lyra.adapters.discord_voice.PCMQueueSource", return_value=new_source
        ):
            await vsm.stream("1", chunks())

        # Assert — push_eof called when is_final chunk received
        new_source.push_eof.assert_called()

    @pytest.mark.asyncio
    async def test_stream_safety_net_push_eof_always_called(self) -> None:
        # Arrange — PERSISTENT so leave() doesn't add a second push_eof call;
        # no chunk has is_final=True, so only the finally safety net fires.
        vsm = _make_vsm()
        session, _, _ = _make_voice_session(mode=VoiceMode.PERSISTENT, is_playing=False)
        vsm._sessions["1"] = session
        new_source = MagicMock(spec=PCMQueueSource)

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk(is_final=False)
            yield _make_chunk(is_final=False)

        # Act — patch PCMQueueSource so stream() assigns the mock as the fresh source
        with patch(
            "lyra.adapters.discord_voice.PCMQueueSource", return_value=new_source
        ):
            await vsm.stream("1", chunks())

        # Assert — safety net: exactly one push_eof call (no is_final chunk received)
        new_source.push_eof.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_transient_autoleave(self) -> None:
        # Arrange — TRANSIENT mode should trigger vsm.leave() after stream ends
        vsm = _make_vsm()
        session, _, _ = _make_voice_session(mode=VoiceMode.TRANSIENT, is_playing=False)
        vsm._sessions["1"] = session
        vsm.leave = AsyncMock()

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk(is_final=True)

        # Act
        await vsm.stream("1", chunks())

        # Assert
        vsm.leave.assert_awaited_once_with("1")

    @pytest.mark.asyncio
    async def test_stream_persistent_no_autoleave(self) -> None:
        # Arrange — PERSISTENT mode must NOT call leave()
        vsm = _make_vsm()
        session, _, _ = _make_voice_session(mode=VoiceMode.PERSISTENT, is_playing=False)
        vsm._sessions["1"] = session
        vsm.leave = AsyncMock()

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk(is_final=True)

        # Act
        await vsm.stream("1", chunks())

        # Assert
        vsm.leave.assert_not_awaited()


# ---------------------------------------------------------------------------
# #256 — DiscordAdapter.render_voice_stream()
# ---------------------------------------------------------------------------


class TestRenderVoiceStream:
    @pytest.mark.asyncio
    async def test_render_voice_stream_calls_vsm_stream(self) -> None:
        # Arrange
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
        adapter._vsm.stream = AsyncMock()
        inbound = _make_discord_inbound(platform_meta={"guild_id": "1"})

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk()

        c = chunks()

        # Act
        await adapter.render_voice_stream(c, inbound)

        # Assert
        adapter._vsm.stream.assert_awaited_once_with("1", c)

    @pytest.mark.asyncio
    async def test_render_voice_stream_non_discord_platform(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — inbound platform is telegram, not discord
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
        adapter._vsm.stream = AsyncMock()

        inbound = InboundMessage(
            id="msg-tg",
            platform="telegram",
            bot_id="main",
            scope_id="chat:42",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            platform_meta={"chat_id": 42},
            trust_level=TrustLevel.TRUSTED,
        )

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk()

        # Act
        with caplog.at_level(logging.WARNING):
            await adapter.render_voice_stream(chunks(), inbound)

        # Assert — vsm.stream not called, WARNING logged
        adapter._vsm.stream.assert_not_awaited()
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    @pytest.mark.asyncio
    async def test_render_voice_stream_missing_guild_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — platform_meta has no guild_id
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
        adapter._vsm.stream = AsyncMock()
        inbound = _make_discord_inbound(platform_meta={})

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield _make_chunk()

        # Act
        with caplog.at_level(logging.WARNING):
            await adapter.render_voice_stream(chunks(), inbound)

        # Assert — vsm.stream not called, WARNING logged
        adapter._vsm.stream.assert_not_awaited()
        assert any(r.levelno >= logging.WARNING for r in caplog.records)
