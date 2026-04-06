"""Tests for DiscordAdapter watch_channels feature (#347)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.adapters.discord import _ALLOW_ALL


class TestWatchChannels:
    """Watch channel feature: messages in designated channels bypass mention filter."""

    @pytest.mark.asyncio
    async def test_watch_channel_message_processed_without_mention(self) -> None:
        """Message in a watch channel is processed even without @mention."""
        from lyra.adapters.discord import DiscordAdapter

        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
            watch_channels=frozenset({333}),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        thread_mock = MagicMock()
        thread_mock.id = 9999
        create_thread_mock = AsyncMock(return_value=thread_mock)

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(
                id=333,
                send=AsyncMock(),
                type=SimpleNamespace(name="text"),
                create_thread=AsyncMock(),
            ),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="https://example.com/article",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],  # no mention
            create_thread=create_thread_mock,
        )

        await adapter.on_message(discord_msg)

        inbound_bus.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_watch_channel_creates_auto_thread(self) -> None:
        """Watch channel message triggers auto-thread creation."""
        from lyra.adapters.discord import DiscordAdapter

        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
            watch_channels=frozenset({333}),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        thread_mock = MagicMock()
        thread_mock.id = 8888
        create_thread_mock = AsyncMock(return_value=thread_mock)

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(
                id=333,
                send=AsyncMock(),
                type=SimpleNamespace(name="text"),
                create_thread=AsyncMock(),
            ),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="https://example.com/article",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
            create_thread=create_thread_mock,
        )

        await adapter.on_message(discord_msg)

        create_thread_mock.assert_awaited_once()
        inbound_bus.put.assert_awaited_once()
        _platform_arg, hub_msg = inbound_bus.put.call_args[0]
        assert hub_msg.platform_meta["thread_id"] == 8888

    @pytest.mark.asyncio
    async def test_non_watch_channel_still_filtered(self) -> None:
        """Message in a non-watch channel without mention is still filtered out."""
        from lyra.adapters.discord import DiscordAdapter

        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
            watch_channels=frozenset({999}),  # different channel
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="https://example.com/article",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
        )

        await adapter.on_message(discord_msg)

        inbound_bus.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_watch_channel_auto_thread_disabled(self) -> None:
        """Watch channel + auto_thread=False: message processed, no thread created."""
        from lyra.adapters.discord import DiscordAdapter

        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=False,
            auth=_ALLOW_ALL,
            watch_channels=frozenset({333}),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        create_thread_mock = AsyncMock()

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(
                id=333,
                send=AsyncMock(),
                type=SimpleNamespace(name="text"),
                create_thread=create_thread_mock,
            ),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="https://example.com/article",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],  # no mention
            create_thread=create_thread_mock,
        )

        await adapter.on_message(discord_msg)

        # Message still processed even though auto_thread=False
        inbound_bus.put.assert_awaited_once()
        # No thread created
        create_thread_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_watch_channel_thread_followup_not_treated_as_watch(self) -> None:
        """Thread message uses owned-thread path, not watch channel."""
        from lyra.adapters.discord import DiscordAdapter

        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
            watch_channels=frozenset({333}),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user
        adapter._owned_threads.add(777)  # thread already owned

        existing_thread = MagicMock(spec=discord.Thread)
        existing_thread.id = 777

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=existing_thread,
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="tell me more about this",
            created_at=datetime.now(timezone.utc),
            id=556,
            mentions=[],
        )

        await adapter.on_message(discord_msg)

        # Processed via owned-thread path, not watch channel
        inbound_bus.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_watch_channel_create_thread_exception_fallback(self) -> None:
        """Watch channel + create_thread raises: message still processed."""
        from lyra.adapters.discord import DiscordAdapter

        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
            watch_channels=frozenset({333}),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        create_thread_mock = AsyncMock(side_effect=Exception("discord unavailable"))

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(
                id=333,
                send=AsyncMock(),
                type=SimpleNamespace(name="text"),
                create_thread=create_thread_mock,
            ),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="https://example.com/article",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
            create_thread=create_thread_mock,
        )

        await adapter.on_message(discord_msg)

        # Message still processed despite create_thread failure
        inbound_bus.put.assert_awaited_once()
