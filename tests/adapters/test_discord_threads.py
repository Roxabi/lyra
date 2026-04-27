"""Tests for DiscordAdapter auto_thread feature (issue #127)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

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
        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
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

        # Assert — inbound_bus.put was called and the InboundMessage has thread_id
        inbound_bus.put.assert_awaited_once()
        _platform_arg, hub_msg = inbound_bus.put.call_args[0]
        from lyra.core.messaging.message import DiscordMeta

        assert isinstance(hub_msg.platform_meta, DiscordMeta)
        assert hub_msg.platform_meta.thread_id == 9999
        assert hub_msg.scope_id == "thread:9999"

    @pytest.mark.asyncio
    async def test_auto_thread_not_created_in_existing_thread(self) -> None:
        """@mention in an existing thread channel does NOT call create_thread()."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
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
        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=False,
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
        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
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
        inbound_bus.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_thread_exception_recovers_partial_thread(self) -> None:
        """create_thread() raises but Discord created the thread: recover thread_id."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
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
        inbound_bus.put.assert_awaited_once()
        _platform_arg, hub_msg = inbound_bus.put.call_args[0]
        assert hub_msg.scope_id == "thread:8888"
        from lyra.core.messaging.message import DiscordMeta

        assert isinstance(hub_msg.platform_meta, DiscordMeta)
        assert hub_msg.platform_meta.thread_id == 8888
        assert 8888 in adapter._owned_threads

    def test_discord_config_auto_thread_default_true(self) -> None:
        """DiscordConfig() has auto_thread=True by default (S5-5)."""
        from lyra.adapters.discord.discord_config import DiscordConfig

        # Arrange / Act
        config = DiscordConfig(token="dummy-token")

        # Assert
        assert config.auto_thread is True


# ---------------------------------------------------------------------------
# Tests for persist_thread_session LRU eviction
# ---------------------------------------------------------------------------


class TestPersistThreadSessionEviction:
    """persist_thread_session evicts the oldest entry when cache is full."""

    @pytest.mark.asyncio
    async def test_persist_thread_session_evicts_oldest_on_full(self) -> None:
        """Cache at 500 entries: adding one more evicts oldest, inserts new."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.adapters.discord.discord_threads import persist_thread_session

        # Arrange — cache pre-filled to the limit (500 entries)
        cache: dict[str, tuple[str, str]] = {str(i): ("s", "p") for i in range(500)}

        mock_store = MagicMock()
        mock_store.update_session = AsyncMock()

        from lyra.core.messaging.message import DiscordMeta

        mock_msg = MagicMock()
        mock_msg.platform_meta = DiscordMeta(
            guild_id=1, channel_id=1, message_id=1, channel_type="text", thread_id=9999
        )

        # Act
        await persist_thread_session(
            mock_store, mock_msg, "new-sess", "new-pool", "bot1", cache
        )

        # Assert — size unchanged (one evicted, one inserted)
        assert len(cache) == 500
        # Oldest key ("0") must have been evicted
        assert "0" not in cache
        # New entry must be present with the correct value
        assert "9999" in cache
        assert cache["9999"] == ("new-sess", "new-pool")

    @pytest.mark.asyncio
    async def test_persist_thread_session_returns_early_for_non_discord_meta(
        self,
    ) -> None:
        """Non-DiscordMeta platform_meta → early return, update_session not called."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.adapters.discord.discord_threads import persist_thread_session
        from lyra.core.messaging.message import TelegramMeta

        cache: dict[str, tuple[str, str]] = {}
        mock_store = MagicMock()
        mock_store.update_session = AsyncMock()

        mock_msg = MagicMock()
        mock_msg.platform_meta = TelegramMeta(chat_id=42)

        await persist_thread_session(
            mock_store, mock_msg, "sess", "pool", "bot1", cache
        )

        mock_store.update_session.assert_not_called()
        assert cache == {}


# ---------------------------------------------------------------------------
# Finding G: persist_thread_claim failure path
# ---------------------------------------------------------------------------


class TestPersistThreadClaimFailurePath:
    """persist_thread_claim raising must not propagate out of handle_message."""

    @pytest.mark.asyncio
    async def test_persist_thread_claim_failure_does_not_prevent_message_processing(
        self,
    ) -> None:
        """persist_thread_claim raising RuntimeError: message still reaches bus."""
        from unittest.mock import patch

        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        inbound_bus = MagicMock()
        inbound_bus.put = AsyncMock()

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=inbound_bus,
            intents=discord.Intents.none(),
            auto_thread=True,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        # Wire a thread store so persist_thread_claim is actually called
        adapter._thread_store = AsyncMock()

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
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        # Patch persist_thread_claim to raise
        with patch(
            "lyra.adapters.discord.discord_inbound.persist_thread_claim",
            AsyncMock(side_effect=RuntimeError("DB error")),
        ):
            # Act — must not raise
            await adapter.on_message(discord_msg)

        # Assert — message still reaches the inbound bus
        inbound_bus.put.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests for retrieve_thread_session
# ---------------------------------------------------------------------------


class TestRetrieveThreadSession:
    """retrieve_thread_session — cache hit/miss, LRU promotion, eviction."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_value(self) -> None:
        """Cache hit: returns value without calling get_session."""
        from unittest.mock import AsyncMock

        from lyra.adapters.discord.discord_threads import retrieve_thread_session

        store = AsyncMock()
        cache: dict[str, tuple[str, str]] = {"123": ("sess-a", "pool-a")}

        result = await retrieve_thread_session(store, "123", "bot1", cache)

        assert result == ("sess-a", "pool-a")
        store.get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_hit_promotes_key_to_end(self) -> None:
        """Cache hit moves the accessed key to end (LRU promotion)."""
        from unittest.mock import AsyncMock

        from lyra.adapters.discord.discord_threads import retrieve_thread_session

        store = AsyncMock()
        cache: dict[str, tuple[str, str]] = {
            "1": ("s1", "p1"),
            "2": ("s2", "p2"),
            "3": ("s3", "p3"),
        }

        await retrieve_thread_session(store, "1", "bot1", cache)

        # "1" should now be at the end (most recently used)
        assert list(cache.keys())[-1] == "1"

    @pytest.mark.asyncio
    async def test_cache_miss_calls_store_and_warms_cache(self) -> None:
        """Cache miss: calls get_session and warms the cache on a hit."""
        from unittest.mock import AsyncMock

        from lyra.adapters.discord.discord_threads import retrieve_thread_session

        store = AsyncMock()
        store.get_session.return_value = ("sess-b", "pool-b")
        cache: dict[str, tuple[str, str]] = {}

        result = await retrieve_thread_session(store, "456", "bot1", cache)

        assert result == ("sess-b", "pool-b")
        store.get_session.assert_awaited_once_with(thread_id="456", bot_id="bot1")
        assert cache["456"] == ("sess-b", "pool-b")

    @pytest.mark.asyncio
    async def test_cache_miss_none_result_does_not_populate_cache(self) -> None:
        """Cache miss with (None, None) from store: cache stays empty."""
        from unittest.mock import AsyncMock

        from lyra.adapters.discord.discord_threads import retrieve_thread_session

        store = AsyncMock()
        store.get_session.return_value = (None, None)
        cache: dict[str, tuple[str, str]] = {}

        result = await retrieve_thread_session(store, "789", "bot1", cache)

        assert result == (None, None)
        assert "789" not in cache

    @pytest.mark.asyncio
    async def test_cache_miss_evicts_oldest_when_full(self) -> None:
        """Cache at 500 entries: miss with a store hit evicts the oldest key."""
        from unittest.mock import AsyncMock

        from lyra.adapters.discord.discord_threads import retrieve_thread_session

        store = AsyncMock()
        store.get_session.return_value = ("sess-new", "pool-new")
        cache: dict[str, tuple[str, str]] = {str(i): ("s", "p") for i in range(500)}
        oldest_key = "0"

        await retrieve_thread_session(store, "999", "bot1", cache)

        assert oldest_key not in cache
        assert "999" in cache
        assert len(cache) == 500


# ---------------------------------------------------------------------------
# Tests for restore_hot_threads
# ---------------------------------------------------------------------------


class TestRestoreHotThreads:
    """restore_hot_threads — str→int conversion, empty path, window calculation."""

    @pytest.mark.asyncio
    async def test_returns_set_of_int_thread_ids(self) -> None:
        """store returns string IDs → result is a set of ints."""
        from unittest.mock import AsyncMock

        from lyra.adapters.discord.discord_threads import restore_hot_threads

        store = AsyncMock()
        store.get_thread_ids.return_value = ["123", "456", "789"]

        result = await restore_hot_threads(store, "bot1", hot_hours=36)

        assert result == {123, 456, 789}

    @pytest.mark.asyncio
    async def test_empty_store_returns_empty_set(self) -> None:
        """store returns no thread IDs → empty set."""
        from unittest.mock import AsyncMock

        from lyra.adapters.discord.discord_threads import restore_hot_threads

        store = AsyncMock()
        store.get_thread_ids.return_value = []

        result = await restore_hot_threads(store, "bot1", hot_hours=36)

        assert result == set()

    @pytest.mark.asyncio
    async def test_passes_active_since_datetime_to_store(self) -> None:
        """get_thread_ids is called with a non-None active_since datetime."""
        from unittest.mock import AsyncMock

        from lyra.adapters.discord.discord_threads import restore_hot_threads

        store = AsyncMock()
        store.get_thread_ids.return_value = []

        await restore_hot_threads(store, "bot1", hot_hours=24)

        call_kwargs = store.get_thread_ids.call_args
        assert call_kwargs is not None
        active_since = call_kwargs.kwargs.get(
            "active_since"
        ) or call_kwargs.args[1]
        assert active_since is not None
