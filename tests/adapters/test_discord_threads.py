"""Tests for DiscordAdapter auto_thread feature (issue #127)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.adapters.discord import _ALLOW_ALL

# ---------------------------------------------------------------------------
# Tests for Discord auto_thread (issue #127)
# ---------------------------------------------------------------------------
# RED-phase tests — describe behaviour implemented by backend-dev in T5/T7.
# These run after the implementation is complete.


class TestDiscordAutoThread:
    """DiscordAdapter creates a thread on @mention in text channels (S5-1..S5-5)."""

    @pytest.mark.asyncio
    async def test_auto_thread_created_on_mention_in_text_channel(self) -> None:
        """@mention in a text channel with auto_thread=True → create_thread() called."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
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
                create_thread=AsyncMock(),  # needed for hasattr check in on_message
            ),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        await adapter.on_message(discord_msg)

        # Assert — create_thread was called once
        create_thread_mock.assert_awaited_once()

        # Assert — hub.inbound_bus.put was called and the InboundMessage has thread_id
        hub.inbound_bus.put.assert_called_once()
        _platform_arg, hub_msg = hub.inbound_bus.put.call_args[0]
        assert hub_msg.platform_meta["thread_id"] == 9999
        assert hub_msg.scope_id == "thread:9999"

    @pytest.mark.asyncio
    async def test_auto_thread_not_created_in_existing_thread(self) -> None:
        """@mention in an existing thread channel does NOT call create_thread()."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        create_thread_mock = AsyncMock()

        # Message already in an existing discord.Thread (isinstance check)
        existing_thread = MagicMock(spec=discord.Thread)
        existing_thread.id = 777

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=existing_thread,
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        await adapter.on_message(discord_msg)

        # Assert — create_thread NOT called when already in a thread
        create_thread_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_thread_disabled(self) -> None:
        """auto_thread=False → create_thread() is never called even on @mention."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=False,
            auth=_ALLOW_ALL,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        create_thread_mock = AsyncMock()

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        await adapter.on_message(discord_msg)

        # Assert — auto_thread=False → no thread created
        create_thread_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_thread_exception_fallback(self) -> None:
        """create_thread() raising Exception: message still processed in original ch."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        # create_thread raises — adapter must fall through and put msg on bus
        create_thread_mock = AsyncMock(side_effect=Exception("discord unavailable"))

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        # Act — must not raise
        await adapter.on_message(discord_msg)

        # Assert — message still processed (bus.put called)
        hub.inbound_bus.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_thread_exception_recovers_partial_thread(self) -> None:
        """create_thread() raises but Discord created the thread: recover thread_id."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        # create_thread raises — but the message has a .thread attached
        # (Discord created it despite the timeout/error)
        create_thread_mock = AsyncMock(side_effect=Exception("timeout after create"))
        partial_thread = SimpleNamespace(id=8888)

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
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
            thread=partial_thread,  # Discord attached the thread despite the error
        )

        # Act — must not raise
        await adapter.on_message(discord_msg)

        # Assert — message processed with recovered thread scope
        hub.inbound_bus.put.assert_called_once()
        _platform_arg, hub_msg = hub.inbound_bus.put.call_args[0]
        assert hub_msg.scope_id == "thread:8888"
        assert hub_msg.platform_meta["thread_id"] == 8888
        assert 8888 in adapter._owned_threads

    def test_discord_config_auto_thread_default_true(self) -> None:
        """DiscordConfig() has auto_thread=True by default (S5-5)."""
        from lyra.adapters.discord import DiscordConfig

        # Arrange / Act
        config = DiscordConfig(token="dummy-token")

        # Assert
        assert config.auto_thread is True
