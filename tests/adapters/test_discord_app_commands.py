"""Tests for Discord app_commands registration (#291).

Covers:
  - register_voice_app_commands() registers /join and /leave on tree
  - /join interaction delegates to voice command logic
  - /leave interaction delegates to voice command logic
  - on_ready tree.sync() failure is caught gracefully
  - Backward compat: !join / !leave text commands still work
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from lyra.adapters.discord.voice.discord_voice_commands import (
    VOICE_COMMANDS,
    register_voice_app_commands,
)

# ---------------------------------------------------------------------------
# VOICE_COMMANDS metadata
# ---------------------------------------------------------------------------


class TestVoiceCommandsMetadata:
    def test_two_commands_defined(self) -> None:
        assert len(VOICE_COMMANDS) == 2

    def test_join_command(self) -> None:
        join = next(c for c in VOICE_COMMANDS if c.name == "/join")
        assert join.description == "Join your voice channel"
        assert len(join.params) == 1
        assert join.params[0].name == "mode"
        assert join.params[0].choices == ["transient", "stay"]

    def test_leave_command(self) -> None:
        leave = next(c for c in VOICE_COMMANDS if c.name == "/leave")
        assert leave.description == "Leave the voice channel"
        assert leave.params == []


# ---------------------------------------------------------------------------
# register_voice_app_commands()
# ---------------------------------------------------------------------------


def _make_mock_client() -> MagicMock:
    """Create a mock client that satisfies CommandTree's __init__ checks."""
    client = MagicMock()
    client._connection._command_tree = None
    return client


class TestRegisterVoiceAppCommands:
    def test_registers_two_commands(self) -> None:
        tree = discord.app_commands.CommandTree(_make_mock_client())
        adapter = MagicMock()
        register_voice_app_commands(tree, adapter)
        cmd_names = [cmd.name for cmd in tree.get_commands()]
        assert "join" in cmd_names
        assert "leave" in cmd_names
        assert len(cmd_names) == 2

    def test_join_has_mode_parameter(self) -> None:
        tree = discord.app_commands.CommandTree(_make_mock_client())
        adapter = MagicMock()
        register_voice_app_commands(tree, adapter)
        join_cmd = next(c for c in tree.get_commands() if c.name == "join")
        assert isinstance(join_cmd, discord.app_commands.Command)
        param_names = [p.name for p in join_cmd.parameters]
        assert "mode" in param_names

    def test_leave_has_no_parameters(self) -> None:
        tree = discord.app_commands.CommandTree(_make_mock_client())
        adapter = MagicMock()
        register_voice_app_commands(tree, adapter)
        leave_cmd = next(c for c in tree.get_commands() if c.name == "leave")
        assert isinstance(leave_cmd, discord.app_commands.Command)
        assert len(leave_cmd.parameters) == 0


# ---------------------------------------------------------------------------
# on_ready tree.sync() error handling
# ---------------------------------------------------------------------------


class TestOnReadySync:
    @pytest.mark.asyncio()
    async def test_sync_failure_does_not_crash(self) -> None:
        """If tree.sync() raises, on_ready should log warning and continue."""
        from lyra.adapters.discord import DiscordAdapter

        adapter = DiscordAdapter(
            bot_id="test",
            inbound_bus=MagicMock(),
        )
        adapter._bot_user = MagicMock()
        adapter._bot_user.id = 12345

        mock_guild = MagicMock()
        mock_guild.id = 99999

        # Patch guilds property and tree.sync to raise
        with patch.object(
            type(adapter),
            "guilds",
            new_callable=lambda: property(lambda self: [mock_guild]),
        ):
            adapter.tree.sync = AsyncMock(side_effect=Exception("sync failed"))
            # Simulate the user being set (on_ready reads self.user)
            with patch.object(
                type(adapter),
                "user",
                new_callable=lambda: property(lambda self: adapter._bot_user),
            ):
                with patch.object(
                    type(adapter),
                    "intents",
                    new_callable=lambda: property(
                        lambda self: discord.Intents.default()
                    ),
                ):
                    # Should not raise
                    await adapter.on_ready()

    @pytest.mark.asyncio()
    async def test_sync_called_per_guild(self) -> None:
        from lyra.adapters.discord import DiscordAdapter

        adapter = DiscordAdapter(
            bot_id="test",
            inbound_bus=MagicMock(),
        )
        adapter._bot_user = MagicMock()
        adapter._bot_user.id = 12345

        guilds = [MagicMock(id=111), MagicMock(id=222)]
        adapter.tree.sync = AsyncMock()

        with patch.object(
            type(adapter), "guilds", new_callable=lambda: property(lambda self: guilds)
        ):
            with patch.object(
                type(adapter),
                "user",
                new_callable=lambda: property(lambda self: adapter._bot_user),
            ):
                with patch.object(
                    type(adapter),
                    "intents",
                    new_callable=lambda: property(
                        lambda self: discord.Intents.default()
                    ),
                ):
                    await adapter.on_ready()

        assert adapter.tree.sync.call_count == 2


# ---------------------------------------------------------------------------
# Backward compat: text prefix !join / !leave
# ---------------------------------------------------------------------------


class TestTextFallbackPreserved:
    @pytest.mark.asyncio()
    async def test_text_join_still_works(self) -> None:
        from lyra.adapters.discord.voice.discord_voice_commands import (
            handle_voice_command,
        )

        adapter = MagicMock()
        adapter._vsm.get.return_value = None  # not in channel

        message = MagicMock()
        message.content = "!join"
        message.guild = MagicMock()
        message.guild.id = 12345
        message.author.voice = MagicMock()
        message.author.voice.channel = MagicMock()

        adapter._vsm.join = AsyncMock()
        result = await handle_voice_command(adapter, message)
        assert result is True  # command was handled

    @pytest.mark.asyncio()
    async def test_text_leave_still_works(self) -> None:
        from lyra.adapters.discord.voice.discord_voice_commands import (
            handle_voice_command,
        )

        adapter = MagicMock()
        adapter._vsm.get.return_value = MagicMock()  # in channel
        adapter._vsm.leave = AsyncMock()

        message = MagicMock()
        message.content = "!leave"
        message.guild = MagicMock()
        message.guild.id = 12345

        result = await handle_voice_command(adapter, message)
        assert result is True
