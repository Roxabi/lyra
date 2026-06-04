"""Unit tests for factory.bootstrap.wiring.kv_watch_channels.

Covers seed_watch_channels (one-shot kv.get path) and the internal helpers
_parse_ids / _kv_key.  No real NATS server is involved — all JetStream
interactions are mocked via unittest.mock.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from factory.bootstrap.wiring.kv_watch_channels import (
    _kv_key,
    _parse_ids,
    publish_watch_channels,
    seed_watch_channels,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(value: bytes) -> MagicMock:
    """Build a minimal KV entry object with the given bytes value."""
    entry = MagicMock()
    entry.value = value
    return entry


def _make_js(kv: MagicMock) -> MagicMock:
    """Build a js mock whose key_value(...) returns *kv*."""
    js = MagicMock()
    js.key_value = AsyncMock(return_value=kv)
    return js


def _make_kv(get_return=None, get_side_effect=None) -> MagicMock:
    """Build a kv mock whose kv.get() returns *get_return* or raises *get_side_effect*."""
    kv = MagicMock()
    if get_side_effect is not None:
        kv.get = AsyncMock(side_effect=get_side_effect)
    else:
        kv.get = AsyncMock(return_value=get_return)
    return kv


# ---------------------------------------------------------------------------
# Tests — seed_watch_channels (kv.get path)
# ---------------------------------------------------------------------------


class TestSeedWatchChannels:
    async def test_returns_frozenset_of_ids_from_valid_entry(self) -> None:
        """(a) kv.get returns entry with value=b'[1,2]' → frozenset({1, 2}).

        Negative guard: deleting the kv.get call or the _parse_ids call causes
        the function to return frozenset() or raise — this test would fail.
        """
        # Arrange
        entry = _make_entry(b"[1, 2]")
        kv = _make_kv(get_return=entry)
        js = _make_js(kv)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset({1, 2})
        kv.get.assert_awaited_once_with("bot.discord.mybot.watch_channels")

    async def test_returns_empty_frozenset_on_key_not_found_error(self) -> None:
        """(b) kv.get raises KeyNotFoundError → frozenset().

        This covers missing key, DEL tombstone, and PURGE tombstone — nats-py
        re-raises all three as KeyNotFoundError inside kv.get().

        Negative guard: removing the KeyNotFoundError handler causes the error
        to propagate instead of returning frozenset().
        """
        # Arrange
        from nats.js.errors import KeyNotFoundError

        kv = _make_kv(get_side_effect=KeyNotFoundError())
        js = _make_js(kv)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset()

    async def test_skips_non_int_channel_id_in_mixed_value(self) -> None:
        """(c) entry value=b'["x",3]' → 'x' skipped, only 3 survives → frozenset({3})."""
        # Arrange
        entry = _make_entry(b'["x", 3]')
        kv = _make_kv(get_return=entry)
        js = _make_js(kv)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset({3})

    async def test_returns_empty_frozenset_on_json_decode_error(self) -> None:
        """(d) entry value=b'not json' → JSONDecodeError guarded → frozenset().

        Negative guard: removing the (json.JSONDecodeError, TypeError) handler
        causes json.loads to raise and propagate instead of returning frozenset().
        """
        # Arrange
        entry = _make_entry(b"not json")
        kv = _make_kv(get_return=entry)
        js = _make_js(kv)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset()

    async def test_returns_empty_frozenset_on_timeout(self) -> None:
        """kv.get raises TimeoutError → frozenset() (timeout guard).

        timeout=0.01 keeps the test fast; asyncio.timeout wraps kv.get so
        a TimeoutError raised by kv.get is treated the same as a real timeout.
        """
        # Arrange — simulate timeout by having kv.get raise TimeoutError
        kv = _make_kv(get_side_effect=TimeoutError())
        js = _make_js(kv)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset()

    async def test_key_not_found_covers_deleted_key_path(self) -> None:
        """KeyNotFoundError from kv.get covers deleted/purged key → frozenset().

        Explicit documentation: nats-py raises KeyNotFoundError for DEL and
        PURGE tombstones when accessed via kv.get(), so a single
        KeyNotFoundError handler covers all three absent-key cases.
        """
        # Arrange
        from nats.js.errors import KeyNotFoundError

        kv = _make_kv(get_side_effect=KeyNotFoundError())
        js = _make_js(kv)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert — deleted key = frozenset(), same as missing key
        assert result == frozenset()

    async def test_js_key_value_called_with_factory_state_bucket(self) -> None:
        """js.key_value is called with 'factory-state' bucket name."""
        # Arrange
        from nats.js.errors import KeyNotFoundError

        kv = _make_kv(get_side_effect=KeyNotFoundError())
        js = _make_js(kv)

        # Act
        await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        js.key_value.assert_awaited_once_with("factory-state")

    async def test_returns_correct_ids_for_telegram_platform(self) -> None:
        """Platform is passed through to the KV key (platform-agnostic)."""
        # Arrange
        entry = _make_entry(b"[7, 8]")
        kv = _make_kv(get_return=entry)
        js = _make_js(kv)

        # Act
        result = await seed_watch_channels(js, "telegram", "bot1", timeout=2.0)

        # Assert
        assert result == frozenset({7, 8})
        kv.get.assert_awaited_once_with("bot.telegram.bot1.watch_channels")


# ---------------------------------------------------------------------------
# Tests — _parse_ids (focused unit test, int-guard)
# ---------------------------------------------------------------------------


class TestParseIds:
    def test_mixed_valid_and_invalid_elements_only_returns_ints(self) -> None:
        """_parse_ids coerces valid items to int and silently skips invalid ones.

        Negative guard: if the try/except (TypeError, ValueError) block is
        removed, int("x") raises ValueError and the function crashes instead
        of returning a partial frozenset — this test would fail.
        """
        # Arrange
        raw = ["1", 2, "x", 3.0]

        # Act
        result = _parse_ids(raw)

        # Assert — "1"→1, 2→2, "x" skipped, 3.0→3
        assert result == frozenset({1, 2, 3})

    def test_empty_list_returns_empty_frozenset(self) -> None:
        # Arrange / Act / Assert
        assert _parse_ids([]) == frozenset()

    def test_all_valid_integers_returned(self) -> None:
        assert _parse_ids([7, 8, 9]) == frozenset({7, 8, 9})

    def test_all_invalid_returns_empty_frozenset(self) -> None:
        assert _parse_ids(["a", "b", None]) == frozenset()

    def test_duplicate_ids_deduplicated_by_frozenset(self) -> None:
        assert _parse_ids([1, 1, 2]) == frozenset({1, 2})


# ---------------------------------------------------------------------------
# Tests — _kv_key (SC4: platform-agnostic key template)
# ---------------------------------------------------------------------------


class TestKvKey:
    def test_discord_key_format(self) -> None:
        """_kv_key builds the correct key for discord/mybot."""
        assert _kv_key("discord", "mybot") == "bot.discord.mybot.watch_channels"

    def test_telegram_key_format(self) -> None:
        """_kv_key builds the correct key for telegram/x — no discord literal."""
        assert _kv_key("telegram", "x") == "bot.telegram.x.watch_channels"

    def test_platform_agnostic_no_discord_literal_in_template(self) -> None:
        """Key template contains no hardcoded platform — SC4.

        Negative guard: if the template were hard-coded to 'discord', the
        telegram assertion above would already fail.  This test makes the
        intent explicit: substitute any platform string and it round-trips.
        """
        # Arrange
        platform = "slack"
        bot_id = "b1"

        # Act
        key = _kv_key(platform, bot_id)

        # Assert
        assert key == "bot.slack.b1.watch_channels"
        assert "discord" not in key


# ---------------------------------------------------------------------------
# Tests — publish_watch_channels (SC4 hub-side write)
# ---------------------------------------------------------------------------


def _make_publish_js(kv: Any) -> MagicMock:
    """Build a js mock whose key_value() returns *kv* (happy path)."""
    js = MagicMock()
    js.key_value = AsyncMock(return_value=kv)
    return js


class TestPublishWatchChannels:
    async def test_single_bot_puts_correct_key_and_value(self) -> None:
        """publish_watch_channels writes key=bot.discord.b1.watch_channels.

        Happy path: js.key_value resolves immediately (bucket exists).
        Asserts kv.put awaited with exact key and JSON-encoded value.
        """
        # Arrange
        kv = MagicMock()
        kv.put = AsyncMock()
        js = _make_publish_js(kv)

        agent_store = MagicMock()
        agent_store.get_bot_settings.return_value = {"watch_channels": [1, 2]}

        # Act
        await publish_watch_channels(js, agent_store, [("discord", "b1")])

        # Assert
        kv.put.assert_awaited_once_with(
            "bot.discord.b1.watch_channels",
            json.dumps([1, 2]).encode(),
        )

    async def test_two_bots_different_platforms_put_distinct_keys(self) -> None:
        """publish_watch_channels is platform-agnostic: two bots → two distinct keys.

        Proves key template does not hard-code 'discord'.
        """
        # Arrange
        kv = MagicMock()
        kv.put = AsyncMock()
        js = _make_publish_js(kv)

        def _get_settings(platform: str, bot_id: str) -> dict:
            return {
                ("discord", "b1"): {"watch_channels": [10, 20]},
                ("telegram", "b2"): {"watch_channels": [30]},
            }[(platform, bot_id)]

        agent_store = MagicMock()
        agent_store.get_bot_settings.side_effect = _get_settings

        # Act
        await publish_watch_channels(
            js, agent_store, [("discord", "b1"), ("telegram", "b2")]
        )

        # Assert — two puts, platform-specific keys
        assert kv.put.await_count == 2
        calls = kv.put.await_args_list
        assert calls[0].args == (
            "bot.discord.b1.watch_channels",
            json.dumps([10, 20]).encode(),
        )
        assert calls[1].args == (
            "bot.telegram.b2.watch_channels",
            json.dumps([30]).encode(),
        )

    async def test_empty_watch_channels_writes_empty_json_array(self) -> None:
        """publish_watch_channels writes '[]' when watch_channels is absent."""
        # Arrange
        kv = MagicMock()
        kv.put = AsyncMock()
        js = _make_publish_js(kv)

        agent_store = MagicMock()
        agent_store.get_bot_settings.return_value = {}  # no watch_channels key

        # Act
        await publish_watch_channels(js, agent_store, [("discord", "b1")])

        # Assert
        kv.put.assert_awaited_once_with(
            "bot.discord.b1.watch_channels",
            b"[]",
        )

    async def test_empty_bots_list_does_not_call_put(self) -> None:
        """publish_watch_channels with no bots performs zero puts."""
        # Arrange
        kv = MagicMock()
        kv.put = AsyncMock()
        js = _make_publish_js(kv)
        agent_store = MagicMock()

        # Act
        await publish_watch_channels(js, agent_store, [])

        # Assert
        kv.put.assert_not_awaited()
