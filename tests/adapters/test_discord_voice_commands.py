"""Tests for discord_voice.py — _handle_voice_command() and
on_message voice command wiring (issue #257)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.discord.voice.discord_voice import (
    VoiceAlreadyActiveError,
    VoiceDependencyError,
    VoiceMode,
)
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import OutboundAudioChunk

# ---------------------------------------------------------------------------
# File-local helpers
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


def _make_chunk(is_final: bool = False) -> OutboundAudioChunk:
    return OutboundAudioChunk(
        chunk_bytes=b"pcm-data",
        session_id="s1",
        chunk_index=0,
        is_final=is_final,
    )


# ---------------------------------------------------------------------------
# #257 — DiscordAdapter._handle_voice_command()
# ---------------------------------------------------------------------------


class TestHandleVoiceCommand:
    @pytest.mark.asyncio
    async def test_join_transient_calls_vsm_join(self) -> None:
        # Arrange
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
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
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )
        join_mock = AsyncMock()
        adapter._vsm.join = join_mock
        msg = _make_message("!help")

        # Act
        result = await adapter._handle_voice_command(msg, TrustLevel.TRUSTED)

        # Assert
        assert result is False


# ---------------------------------------------------------------------------
# on_message voice command wiring
# ---------------------------------------------------------------------------


class TestOnMessageVoiceCommandWiring:
    @pytest.mark.asyncio
    async def test_voice_command_in_guild_skips_hub_push(self) -> None:
        # Arrange — !join in a guild text channel should NOT reach inbound_bus
        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()
        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
        )
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

        # Assert — inbound bus was NOT called
        inbound_bus.put.assert_not_called()
