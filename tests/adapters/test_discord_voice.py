"""Tests for discord_voice.py — VoiceSessionManager, PCMQueueSource (issue #255)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import lyra.adapters.discord_voice as dv_module
from lyra.adapters.discord import _ALLOW_ALL, DiscordAdapter
from lyra.adapters.discord_voice import (
    PCMQueueSource,
    VoiceAlreadyActiveError,
    VoiceDependencyError,
    VoiceMode,
    VoiceSession,
    VoiceSessionManager,
    _check_voice_deps,
)
from lyra.core.auth import TrustLevel
from lyra.core.message import InboundMessage, OutboundAudioChunk

_FRAME = 3840


class TestPCMQueueSource:
    def test_exact_frame(self) -> None:
        src = PCMQueueSource()
        src.push(b"x" * _FRAME)
        src.push_eof()
        assert src.read() == b"x" * _FRAME
        assert src.read() == b""

    def test_reframing_multi(self) -> None:
        src = PCMQueueSource()
        src.push(b"a" * (_FRAME * 2 + 100))
        src.push_eof()
        frames = []
        while True:
            f = src.read()
            if f == b"":
                break
            frames.append(f)
        assert len(frames) == 3
        assert all(len(f) == _FRAME for f in frames)
        assert frames[-1] == b"a" * 100 + b"\x00" * (_FRAME - 100)

    def test_silence_frame_on_empty_queue(self) -> None:
        src = PCMQueueSource()
        silence = src.read()
        assert silence == b"\x00" * _FRAME

    def test_eof_after_push_eof(self) -> None:
        src = PCMQueueSource()
        src.push_eof()
        assert src.read() == b""

    def test_double_push_eof_harmless(self) -> None:
        src = PCMQueueSource()
        src.push_eof()
        src.push_eof()
        assert src.read() == b""  # first sentinel consumed
        assert src.read() == b""  # second sentinel also returns b"" cleanly

    def test_is_opus_false(self) -> None:
        assert PCMQueueSource().is_opus() is False


class TestVoiceSession:
    def test_is_active_true(self) -> None:
        vc = MagicMock()
        vc.is_connected.return_value = True
        session = VoiceSession(
            guild_id="1",
            voice_client=vc,
            channel_id=100,
            mode=VoiceMode.TRANSIENT,
            source=PCMQueueSource(),
        )
        assert session.is_active() is True

    def test_is_active_false(self) -> None:
        vc = MagicMock()
        vc.is_connected.return_value = False
        session = VoiceSession(
            guild_id="1",
            voice_client=vc,
            channel_id=100,
            mode=VoiceMode.PERSISTENT,
            source=PCMQueueSource(),
        )
        assert session.is_active() is False


# ---------------------------------------------------------------------------
# V2 — Dependency check
# ---------------------------------------------------------------------------


class TestVoiceDeps:
    def setup_method(self) -> None:
        dv_module._deps_checked = False  # reset between tests

    def test_missing_libopus(self) -> None:
        with (
            patch(
                "lyra.adapters.discord_voice.discord.opus.is_loaded",
                return_value=False,
            ),
            patch(
                "lyra.adapters.discord_voice.ctypes.util.find_library",
                return_value=None,
            ),
            patch(
                "lyra.adapters.discord_voice.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
        ):
            with pytest.raises(VoiceDependencyError, match="libopus"):
                _check_voice_deps()

    def test_missing_ffmpeg(self) -> None:
        with (
            patch(
                "lyra.adapters.discord_voice.discord.opus.is_loaded",
                return_value=True,
            ),
            patch("lyra.adapters.discord_voice.shutil.which", return_value=None),
        ):
            with pytest.raises(VoiceDependencyError, match="ffmpeg"):
                _check_voice_deps()

    def test_passes_when_deps_present(self) -> None:
        with (
            patch(
                "lyra.adapters.discord_voice.discord.opus.is_loaded",
                return_value=True,
            ),
            patch(
                "lyra.adapters.discord_voice.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
        ):
            _check_voice_deps()
            assert dv_module._deps_checked is True

    def test_skips_on_second_call(self) -> None:
        dv_module._deps_checked = True
        # No patches — if checks ran they'd fail. Passing = correctly skipped.
        _check_voice_deps()


# ---------------------------------------------------------------------------
# V3 — VoiceSessionManager
# ---------------------------------------------------------------------------


def _make_vsm() -> VoiceSessionManager:
    return VoiceSessionManager()


def _make_guild(guild_id: int = 1) -> MagicMock:
    g = MagicMock()
    g.id = guild_id
    return g


def _make_channel() -> tuple[MagicMock, MagicMock]:
    vc = AsyncMock()
    vc.is_connected.return_value = True
    ch = MagicMock()
    ch.id = 100
    ch.connect = AsyncMock(return_value=vc)
    return ch, vc


class TestVoiceSessionManager:
    @pytest.mark.asyncio
    async def test_join_stores_session(self) -> None:
        vsm = _make_vsm()
        guild = _make_guild()
        channel, _ = _make_channel()
        with patch("lyra.adapters.discord_voice._check_voice_deps"):
            session = await vsm.join(guild, channel, VoiceMode.TRANSIENT)
        assert vsm.get("1") is session

    @pytest.mark.asyncio
    async def test_join_duplicate_raises(self) -> None:
        vsm = _make_vsm()
        guild = _make_guild()
        channel, _ = _make_channel()
        with patch("lyra.adapters.discord_voice._check_voice_deps"):
            await vsm.join(guild, channel, VoiceMode.TRANSIENT)
            with pytest.raises(VoiceAlreadyActiveError):
                channel2, _ = _make_channel()
                await vsm.join(guild, channel2, VoiceMode.TRANSIENT)

    @pytest.mark.asyncio
    async def test_leave_disconnects_and_removes(self) -> None:
        vsm = _make_vsm()
        guild = _make_guild()
        channel, vc = _make_channel()
        with patch("lyra.adapters.discord_voice._check_voice_deps"):
            session = await vsm.join(guild, channel, VoiceMode.TRANSIENT)
        mock_source = MagicMock(spec=PCMQueueSource)
        session.source = mock_source
        await vsm.leave("1")
        mock_source.push_eof.assert_called_once()
        vc.disconnect.assert_called_once()
        assert vsm.get("1") is None

    @pytest.mark.asyncio
    async def test_leave_no_session_is_noop(self) -> None:
        vsm = _make_vsm()
        await vsm.leave("999")  # no error

    def test_invalidate_removes_without_disconnect(self) -> None:
        vsm = _make_vsm()
        vc = MagicMock()
        vsm._sessions["1"] = VoiceSession(
            guild_id="1",
            voice_client=vc,
            channel_id=100,
            mode=VoiceMode.PERSISTENT,
            source=PCMQueueSource(),
        )
        vsm.invalidate("1")
        vc.disconnect.assert_not_called()
        assert vsm.get("1") is None

    def test_get_returns_none_for_unknown_guild(self) -> None:
        vsm = _make_vsm()
        assert vsm.get("unknown") is None


# ---------------------------------------------------------------------------
# V4 — on_voice_state_update hook in DiscordAdapter
# ---------------------------------------------------------------------------


def _make_adapter_with_bot() -> DiscordAdapter:
    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main")
    bot_user = MagicMock()
    bot_user.id = 999
    adapter._bot_user = bot_user
    return adapter


def _make_member(member_id: int, guild_id: int) -> MagicMock:
    m = MagicMock()
    m.id = member_id
    m.guild = MagicMock()
    m.guild.id = guild_id
    return m


class TestOnVoiceStateUpdate:
    @pytest.mark.asyncio
    async def test_bot_disconnect_calls_invalidate(self) -> None:
        adapter = _make_adapter_with_bot()
        adapter._vsm.invalidate = MagicMock()
        member = _make_member(member_id=999, guild_id=1)
        after = MagicMock(channel=None)
        await adapter.on_voice_state_update(member, MagicMock(), after)
        adapter._vsm.invalidate.assert_called_once_with("1")

    @pytest.mark.asyncio
    async def test_bot_move_does_not_invalidate(self) -> None:
        adapter = _make_adapter_with_bot()
        adapter._vsm.invalidate = MagicMock()
        member = _make_member(member_id=999, guild_id=1)
        after = MagicMock(channel=MagicMock())  # still in a channel
        await adapter.on_voice_state_update(member, MagicMock(), after)
        adapter._vsm.invalidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_member_disconnect_does_not_invalidate(self) -> None:
        adapter = _make_adapter_with_bot()
        adapter._vsm.invalidate = MagicMock()
        member = _make_member(member_id=42, guild_id=1)
        after = MagicMock(channel=None)
        await adapter.on_voice_state_update(member, MagicMock(), after)
        adapter._vsm.invalidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_member_move_does_not_invalidate(self) -> None:
        adapter = _make_adapter_with_bot()
        adapter._vsm.invalidate = MagicMock()
        member = _make_member(member_id=42, guild_id=1)
        after = MagicMock(channel=MagicMock())
        await adapter.on_voice_state_update(member, MagicMock(), after)
        adapter._vsm.invalidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_not_ready_does_not_invalidate(self) -> None:
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        # _bot_user is None (not ready yet)
        adapter._vsm.invalidate = MagicMock()
        member = _make_member(member_id=999, guild_id=1)
        after = MagicMock(channel=None)
        await adapter.on_voice_state_update(member, MagicMock(), after)
        adapter._vsm.invalidate.assert_not_called()


# ---------------------------------------------------------------------------
# #256 — VoiceSessionManager.stream()
# ---------------------------------------------------------------------------


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
        session, vc, source = _make_voice_session(is_playing=True)
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
        session, vc, _ = _make_voice_session(is_playing=False)
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
        session, vc, _ = _make_voice_session(
            mode=VoiceMode.PERSISTENT, is_playing=False
        )
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
        session, vc, source = _make_voice_session(
            mode=VoiceMode.TRANSIENT, is_playing=False
        )
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
        session, vc, source = _make_voice_session(
            mode=VoiceMode.PERSISTENT, is_playing=False
        )
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


def _make_discord_inbound(
    platform_meta: dict | None = None,
) -> InboundMessage:
    """Build a minimal discord InboundMessage for render_voice_stream tests."""
    from datetime import datetime, timezone

    from lyra.core.auth import TrustLevel
    from lyra.core.message import InboundMessage

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


class TestRenderVoiceStream:
    @pytest.mark.asyncio
    async def test_render_voice_stream_calls_vsm_stream(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
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
        from datetime import datetime, timezone

        from lyra.core.auth import TrustLevel
        from lyra.core.message import InboundMessage

        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
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
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
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


# ---------------------------------------------------------------------------
# #257 — DiscordAdapter._handle_voice_command()
# ---------------------------------------------------------------------------


def _make_message(
    content: str,
    guild_id: int = 1,
    author_voice_channel: MagicMock | None = MagicMock(),
) -> MagicMock:
    """Build a minimal discord.Message mock for voice command tests."""
    msg = MagicMock()
    msg.id = 9001
    msg.content = content
    msg.reply = AsyncMock()

    guild = MagicMock()
    guild.id = guild_id
    msg.guild = guild

    voice_state = MagicMock()
    voice_state.channel = author_voice_channel
    msg.author = MagicMock()
    msg.author.voice = voice_state

    return msg


class TestHandleVoiceCommand:
    @pytest.mark.asyncio
    async def test_join_transient_calls_vsm_join(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock()
        adapter._vsm.join = join_mock
        voice_ch = MagicMock()
        msg = _make_message("!join", author_voice_channel=voice_ch)

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        join_mock.assert_awaited_once_with(msg.guild, voice_ch, VoiceMode.TRANSIENT)

    @pytest.mark.asyncio
    async def test_join_slash_prefix_calls_vsm_join(self) -> None:
        # Arrange — /join (slash prefix) must route identically to !join
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock()
        adapter._vsm.join = join_mock
        voice_ch = MagicMock()
        msg = _make_message("/join", author_voice_channel=voice_ch)

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        join_mock.assert_awaited_once_with(msg.guild, voice_ch, VoiceMode.TRANSIENT)

    @pytest.mark.asyncio
    async def test_join_stay_calls_vsm_join_persistent(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock()
        adapter._vsm.join = join_mock
        voice_ch = MagicMock()
        msg = _make_message("!join stay", author_voice_channel=voice_ch)

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        join_mock.assert_awaited_once_with(msg.guild, voice_ch, VoiceMode.PERSISTENT)

    @pytest.mark.asyncio
    async def test_join_stay_case_insensitive_is_persistent(self) -> None:
        # Arrange — "!join STAY" (uppercase) must map to PERSISTENT
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock()
        adapter._vsm.join = join_mock
        voice_ch = MagicMock()
        msg = _make_message("!join STAY", author_voice_channel=voice_ch)

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        join_mock.assert_awaited_once_with(msg.guild, voice_ch, VoiceMode.PERSISTENT)

    @pytest.mark.asyncio
    async def test_join_stay_with_extra_args_is_persistent(self) -> None:
        # Arrange — "!join stay please" should still be PERSISTENT
        # (prefix match on first token)
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock()
        adapter._vsm.join = join_mock
        voice_ch = MagicMock()
        msg = _make_message("!join stay please", author_voice_channel=voice_ch)

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        join_mock.assert_awaited_once_with(msg.guild, voice_ch, VoiceMode.PERSISTENT)

    @pytest.mark.asyncio
    async def test_leave_with_active_session_disconnects_and_replies(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        leave_mock = AsyncMock()
        adapter._vsm.leave = leave_mock
        adapter._vsm.get = MagicMock(return_value=MagicMock())  # session exists
        msg = _make_message("!leave")

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        leave_mock.assert_awaited_once_with(str(msg.guild.id))
        msg.reply.assert_awaited_once_with("Left the voice channel.")

    @pytest.mark.asyncio
    async def test_leave_without_session_replies_not_in_channel(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        leave_mock = AsyncMock()
        adapter._vsm.leave = leave_mock
        adapter._vsm.get = MagicMock(return_value=None)  # no session
        msg = _make_message("!leave")

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        leave_mock.assert_not_awaited()
        msg.reply.assert_awaited_once_with("I'm not in a voice channel.")

    @pytest.mark.asyncio
    async def test_non_voice_command_returns_false(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock()
        leave_mock = AsyncMock()
        adapter._vsm.join = join_mock
        adapter._vsm.leave = leave_mock
        msg = _make_message("hello world")

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is False
        join_mock.assert_not_awaited()
        leave_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_join_user_not_in_voice_channel_replies_error(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock()
        adapter._vsm.join = join_mock
        msg = _make_message("!join", author_voice_channel=None)

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        join_mock.assert_not_awaited()
        msg.reply.assert_awaited_once_with("Join a voice channel first.")

    @pytest.mark.asyncio
    async def test_join_user_no_voice_state_replies_error(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock()
        adapter._vsm.join = join_mock
        msg = _make_message("!join")
        msg.author.voice = None  # no voice attribute at all

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        join_mock.assert_not_awaited()
        msg.reply.assert_awaited_once_with("Join a voice channel first.")

    @pytest.mark.asyncio
    async def test_join_already_active_replies_error(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock(side_effect=VoiceAlreadyActiveError("1"))
        adapter._vsm.join = join_mock
        voice_ch = MagicMock()
        msg = _make_message("!join", author_voice_channel=voice_ch)

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        msg.reply.assert_awaited_once_with("Already in a voice channel.")

    @pytest.mark.asyncio
    async def test_join_already_active_reply_raises_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — inner reply() raises; log.warning must fire, True must return
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock(side_effect=VoiceAlreadyActiveError("1"))
        adapter._vsm.join = join_mock
        voice_ch = MagicMock()
        msg = _make_message("!join", author_voice_channel=voice_ch)
        msg.reply = AsyncMock(side_effect=RuntimeError("discord unavailable"))

        # Act
        with caplog.at_level(logging.WARNING):
            result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    @pytest.mark.asyncio
    async def test_join_dependency_error_logs_and_replies(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock(side_effect=VoiceDependencyError("libopus missing"))
        adapter._vsm.join = join_mock
        voice_ch = MagicMock()
        msg = _make_message("!join", author_voice_channel=voice_ch)

        # Act
        with caplog.at_level(logging.ERROR):
            result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is True
        assert any("libopus missing" in r.message for r in caplog.records)
        msg.reply.assert_awaited_once_with("Voice is not available right now.")

    @pytest.mark.asyncio
    async def test_other_command_returns_false(self) -> None:
        # Arrange
        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        join_mock = AsyncMock()
        adapter._vsm.join = join_mock
        msg = _make_message("!help")

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is False


class TestOnMessageVoiceCommandWiring:
    @pytest.mark.asyncio
    async def test_voice_command_in_guild_skips_hub_push(self) -> None:
        # Arrange — !join in a guild text channel should NOT reach hub.inbound_bus
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put_nowait = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")
        adapter._auth = _ALLOW_ALL  # permit the message
        # Mock _handle_voice_command to return True (simulates voice command handled)
        adapter._handle_voice_command = AsyncMock(return_value=True)

        # Build a minimal guild message mock
        message = MagicMock()
        message.author.bot = False
        message.author.id = 42
        message.author.roles = []
        message.guild = MagicMock()
        message.attachments = []
        message.content = "!join"
        # not a Thread, not ForumChannel, not VoiceChannel
        message.channel = MagicMock(spec=[])

        # Act
        await adapter.on_message(message)

        # Assert — hub inbound bus was NOT called
        hub.inbound_bus.put_nowait.assert_not_called()
