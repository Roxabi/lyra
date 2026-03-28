"""Tests for DiscordAdapter vault_channels feature."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.adapters.discord import _ALLOW_ALL


class TestVaultChannels:
    """Vault channel feature: messages in designated channels are auto-saved to vault."""

    @pytest.mark.asyncio
    async def test_vault_channel_message_processed_without_mention(self) -> None:
        """Message in a vault channel is processed even without @mention."""
        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
            vault_channels=frozenset({444}),
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(
                id=444,
                send=AsyncMock(),
                type=SimpleNamespace(name="text"),
                create_thread=AsyncMock(),
            ),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="a note I want to save",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],  # no mention
            create_thread=AsyncMock(),
        )

        await adapter.on_message(discord_msg)

        hub.inbound_bus.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_vault_channel_text_rewritten_as_add_vault_command(self) -> None:
        """Message in a vault channel has its text rewritten to /add-vault <content>."""
        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
            vault_channels=frozenset({444}),
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(
                id=444,
                send=AsyncMock(),
                type=SimpleNamespace(name="text"),
                create_thread=AsyncMock(),
            ),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="remember this important thing",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
            create_thread=AsyncMock(),
        )

        await adapter.on_message(discord_msg)

        hub.inbound_bus.put.assert_called_once()
        _platform_arg, hub_msg = hub.inbound_bus.put.call_args[0]
        assert hub_msg.text == "/add-vault remember this important thing"

    @pytest.mark.asyncio
    async def test_vault_channel_no_auto_thread_created(self) -> None:
        """Vault channel messages do NOT create an auto-thread."""
        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,  # auto_thread enabled globally
            auth=_ALLOW_ALL,
            vault_channels=frozenset({444}),
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        create_thread_mock = AsyncMock()

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(
                id=444,
                send=AsyncMock(),
                type=SimpleNamespace(name="text"),
                create_thread=AsyncMock(),
            ),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="no thread please",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
            create_thread=create_thread_mock,
        )

        await adapter.on_message(discord_msg)

        # Message is processed
        hub.inbound_bus.put.assert_called_once()
        # But no thread is created
        create_thread_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_vault_channel_not_processed(self) -> None:
        """Message in a non-vault channel without mention is still filtered out."""
        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
            vault_channels=frozenset({999}),  # different channel
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=444, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="unrelated message",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
        )

        await adapter.on_message(discord_msg)

        hub.inbound_bus.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_vault_channel_thread_message_not_treated_as_vault(self) -> None:
        """Thread messages in a vault channel are excluded (threads are not channels)."""
        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
            vault_channels=frozenset({444}),
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        thread_channel = MagicMock(spec=discord.Thread)
        thread_channel.id = 444  # same ID but a Thread type

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=thread_channel,
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="this is inside a thread",
            created_at=datetime.now(timezone.utc),
            id=556,
            mentions=[],
        )

        # Not processed — no mention, not owned thread, vault channel check excludes threads
        await adapter.on_message(discord_msg)

        hub.inbound_bus.put.assert_not_called()
