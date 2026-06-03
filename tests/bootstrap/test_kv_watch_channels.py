"""Unit tests for factory.bootstrap.wiring.kv_watch_channels.

Covers seed_watch_channels (one-shot KV read) and the internal helpers
_parse_ids / _skip_tombstone.  No real NATS server is involved — all
JetStream interactions are mocked via unittest.mock.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from factory.bootstrap.wiring.kv_watch_channels import (
    _parse_ids,
    seed_watch_channels,
)

# ---------------------------------------------------------------------------
# Helpers — async iterator that yields a fixed sequence of entries
# ---------------------------------------------------------------------------


class _FakeWatcher:
    """Async-iterator and async-context-manager that yields *entries* in order.

    seed_watch_channels calls:
        watcher = await kv.watch(key)          → _FakeWatcher instance
        async for entry in watcher: ...        → yields from self._entries
        await watcher.stop()                   → no-op (tracked via mock)
    """

    def __init__(self, entries: list[Any]) -> None:
        self._entries = iter(entries)
        self.stop = AsyncMock()

    def __aiter__(self) -> "_FakeWatcher":
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._entries)
        except StopIteration:
            raise StopAsyncIteration


def _make_entry(value: bytes, operation: Any = None) -> MagicMock:
    """Build a minimal KV entry object."""
    entry = MagicMock()
    entry.value = value
    entry.operation = operation
    return entry


def _make_js(watcher: _FakeWatcher) -> MagicMock:
    """Build a js mock whose key_value(...).watch(...) returns *watcher*."""
    kv = MagicMock()
    kv.watch = AsyncMock(return_value=watcher)
    js = MagicMock()
    js.key_value = AsyncMock(return_value=kv)
    return js


# ---------------------------------------------------------------------------
# Tests — seed_watch_channels
# ---------------------------------------------------------------------------


class TestSeedWatchChannels:
    async def test_returns_frozenset_of_ids_from_first_real_put_entry(self) -> None:
        """(a) First real entry with value=[1,2] and no tombstone operation → {1, 2}."""
        # Arrange
        entry = _make_entry(b"[1, 2]", operation=None)
        watcher = _FakeWatcher([entry])
        js = _make_js(watcher)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset({1, 2})
        watcher.stop.assert_awaited_once()

    async def test_returns_empty_frozenset_after_none_sentinel_and_timeout(
        self,
    ) -> None:
        """(b) None sentinel then no further entries → frozenset() on timeout.

        timeout=0.05 keeps the test fast without violating the test_sleep gate
        (no real sleep call — asyncio.timeout drives the wait).
        """
        # Arrange — only a None sentinel; no subsequent real entry
        watcher = _FakeWatcher([None])
        js = _make_js(watcher)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=0.05)

        # Assert
        assert result == frozenset()
        watcher.stop.assert_awaited_once()

    async def test_returns_empty_frozenset_on_purge_tombstone(self) -> None:
        """(c) First real entry has operation='PURGE' → tombstone → frozenset().

        json.loads must never be called on the (empty/tombstone) value.
        Verified implicitly: entry.value is b"" (json.loads(b"") would raise),
        but the function returns before reaching that line.
        """
        # Arrange — tombstone entry; value intentionally unparseable
        entry = _make_entry(b"", operation="PURGE")
        watcher = _FakeWatcher([entry])
        js = _make_js(watcher)

        # Act — must not raise even though entry.value is not valid JSON
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset()
        watcher.stop.assert_awaited_once()

    async def test_skips_non_int_channel_id_in_mixed_value(self) -> None:
        """(d) value=['x', 3] → 'x' is skipped, only 3 survives → frozenset({3})."""
        # Arrange
        entry = _make_entry(b'["x", 3]', operation=None)
        watcher = _FakeWatcher([entry])
        js = _make_js(watcher)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset({3})
        watcher.stop.assert_awaited_once()

    async def test_put_operation_string_treated_as_real_entry(self) -> None:
        """First real entry with operation='PUT' (explicit string) → ids returned."""
        # Arrange
        entry = _make_entry(b"[10, 20]", operation="PUT")
        watcher = _FakeWatcher([entry])
        js = _make_js(watcher)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset({10, 20})

    async def test_del_tombstone_returns_empty_frozenset(self) -> None:
        """DEL operation is a tombstone — must return frozenset() like PURGE."""
        # Arrange
        entry = _make_entry(b"", operation="DEL")
        watcher = _FakeWatcher([entry])
        js = _make_js(watcher)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset()

    async def test_none_sentinel_then_real_entry_returns_ids(self) -> None:
        """None sentinel followed by a real entry → ids from real entry returned."""
        # Arrange
        real_entry = _make_entry(b"[5, 6]", operation=None)
        watcher = _FakeWatcher([None, real_entry])
        js = _make_js(watcher)

        # Act
        result = await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert
        assert result == frozenset({5, 6})

    async def test_stop_is_always_called_even_on_tombstone(self) -> None:
        """watcher.stop() is always called (finally block) — negative tombstone path."""
        # Arrange
        entry = _make_entry(b"", operation="PURGE")
        watcher = _FakeWatcher([entry])
        js = _make_js(watcher)

        # Act
        await seed_watch_channels(js, "discord", "mybot", timeout=2.0)

        # Assert — stop called exactly once via finally
        watcher.stop.assert_awaited_once()


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
