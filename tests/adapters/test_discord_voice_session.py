"""Tests for discord_voice.py — PCMQueueSource, VoiceSession, VoiceDeps,
VoiceSessionManager, and on_voice_state_update (issue #255)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import lyra.adapters.discord.voice.discord_voice as dv_module
from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.discord.voice.discord_voice import (
    PCMQueueSource,
    VoiceAlreadyActiveError,
    VoiceDependencyError,
    VoiceMode,
    VoiceSession,
    VoiceSessionManager,
    _check_voice_deps,
)

_FRAME = 3840


# ---------------------------------------------------------------------------
# PCMQueueSource
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# VoiceSession
# ---------------------------------------------------------------------------


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
                "lyra.adapters.discord.voice.discord_voice.discord.opus.is_loaded",
                return_value=False,
            ),
            patch(
                "lyra.adapters.discord.voice.discord_voice.ctypes.util.find_library",
                return_value=None,
            ),
            patch(
                "lyra.adapters.discord.voice.discord_voice.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
        ):
            with pytest.raises(VoiceDependencyError, match="libopus"):
                _check_voice_deps()

    def test_missing_ffmpeg(self) -> None:
        with (
            patch(
                "lyra.adapters.discord.voice.discord_voice.discord.opus.is_loaded",
                return_value=True,
            ),
            patch(
                "lyra.adapters.discord.voice.discord_voice.shutil.which",
                return_value=None,
            ),  # noqa: E501
        ):
            with pytest.raises(VoiceDependencyError, match="ffmpeg"):
                _check_voice_deps()

    def test_passes_when_deps_present(self) -> None:
        with (
            patch(
                "lyra.adapters.discord.voice.discord_voice.discord.opus.is_loaded",
                return_value=True,
            ),
            patch(
                "lyra.adapters.discord.voice.discord_voice.shutil.which",
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
        with patch("lyra.adapters.discord.voice.discord_voice._check_voice_deps"):
            session = await vsm.join(guild, channel, VoiceMode.TRANSIENT)
        assert vsm.get("1") is session

    @pytest.mark.asyncio
    async def test_join_duplicate_raises(self) -> None:
        vsm = _make_vsm()
        guild = _make_guild()
        channel, _ = _make_channel()
        with patch("lyra.adapters.discord.voice.discord_voice._check_voice_deps"):
            await vsm.join(guild, channel, VoiceMode.TRANSIENT)
            with pytest.raises(VoiceAlreadyActiveError):
                channel2, _ = _make_channel()
                await vsm.join(guild, channel2, VoiceMode.TRANSIENT)

    @pytest.mark.asyncio
    async def test_leave_disconnects_and_removes(self) -> None:
        vsm = _make_vsm()
        guild = _make_guild()
        channel, vc = _make_channel()
        with patch("lyra.adapters.discord.voice.discord_voice._check_voice_deps"):
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
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
    )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
        # _bot_user is None (not ready yet)
        adapter._vsm.invalidate = MagicMock()
        member = _make_member(member_id=999, guild_id=1)
        after = MagicMock(channel=None)
        await adapter.on_voice_state_update(member, MagicMock(), after)
        adapter._vsm.invalidate.assert_not_called()
