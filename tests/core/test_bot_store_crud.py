"""BotStore CRUD, cache, reconnect, and from_db_row tests (issue #1414, T8)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lyra.core.agent.bot_models import BotRow
from lyra.infrastructure.stores.bot_store import BotStore
from tests.helpers.bot_store import make_bot_row, make_bot_store

# ---------------------------------------------------------------------------
# TestBotStoreConnect
# ---------------------------------------------------------------------------


class TestBotStoreConnect:
    """BotStore.connect() — creates tables, is idempotent, guards after close."""

    async def test_connect_creates_bots_table(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_bot_store(tmp_path)
        try:
            # Act — connect() already called by make_bot_store
            assert store._db is not None

            # Assert — bots table must exist
            async with store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                rows = await cur.fetchall()
            found = {row[0] for row in rows}
            assert "bots" in found, f"expected 'bots' table, found {found!r}"
        finally:
            await store.close()

    async def test_connect_idempotent(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_bot_store(tmp_path)
        try:
            # Act — call connect() a second time
            await store.connect()  # must not raise
        finally:
            await store.close()

    async def test_get_before_connect_raises(self, tmp_path: Path) -> None:
        # Arrange
        store = BotStore(db_path=str(tmp_path / "bots.db"))

        # Act + Assert — get() before connect() must raise RuntimeError
        with pytest.raises(RuntimeError, match="connect"):
            store.get("telegram", "main")

    async def test_close_then_get_raises(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_bot_store(tmp_path)
        await store.close()

        # Act + Assert — get() after close() must raise RuntimeError
        with pytest.raises(RuntimeError, match="connect"):
            store.get("telegram", "main")


# ---------------------------------------------------------------------------
# TestBotCRUD
# ---------------------------------------------------------------------------


class TestBotCRUD:
    """BotStore CRUD: upsert / get / get_all / delete."""

    async def test_get_missing_returns_none(self, bot_store: BotStore) -> None:
        # Act
        result = bot_store.get("telegram", "nonexistent")

        # Assert
        assert result is None

    async def test_upsert_and_get(self, bot_store: BotStore) -> None:
        # Arrange
        row = make_bot_row("telegram", "main", "a")

        # Act
        await bot_store.upsert(row)
        result = bot_store.get("telegram", "main")

        # Assert
        assert result is not None
        assert result.platform == "telegram"
        assert result.bot_id == "main"
        assert result.agent == "a"

    async def test_upsert_overwrites_existing(self, bot_store: BotStore) -> None:
        # Arrange
        await bot_store.upsert(make_bot_row("telegram", "main", "a"))
        row2 = make_bot_row("telegram", "main", "b")

        # Act
        await bot_store.upsert(row2)
        result = bot_store.get("telegram", "main")

        # Assert
        assert result is not None
        assert result.agent == "b"

    async def test_upsert_updates_updated_at(self, bot_store: BotStore) -> None:
        # Arrange
        row = make_bot_row("telegram", "main", "a")

        # Act — first upsert
        with patch(
            "lyra.infrastructure.stores.bot_store._utc_now_iso",
            return_value="2024-01-01T00:00:00+00:00",
        ):
            await bot_store.upsert(row)
        first = bot_store.get("telegram", "main")
        assert first is not None

        # Act — second upsert with different timestamp
        with patch(
            "lyra.infrastructure.stores.bot_store._utc_now_iso",
            return_value="2024-01-02T00:00:00+00:00",
        ):
            await bot_store.upsert(row)
        second = bot_store.get("telegram", "main")
        assert second is not None

        # Assert — updated_at changed
        assert first.updated_at == "2024-01-01T00:00:00+00:00"
        assert second.updated_at == "2024-01-02T00:00:00+00:00"

    async def test_upsert_cache_defensive_copy(self, bot_store: BotStore) -> None:
        # Arrange
        owners = ["alice"]
        row = make_bot_row("telegram", "main", "a", owner_users=owners)

        # Act
        await bot_store.upsert(row)
        owners.append("bob")  # mutate original list
        result = bot_store.get("telegram", "main")

        # Assert — cache was not mutated
        assert result is not None
        assert result.owner_users == ["alice"]

    async def test_delete_removes_row(self, bot_store: BotStore) -> None:
        # Arrange
        await bot_store.upsert(make_bot_row("telegram", "main", "a"))

        # Act
        await bot_store.delete("telegram", "main")

        # Assert
        assert bot_store.get("telegram", "main") is None

    async def test_delete_missing_is_noop(self, bot_store: BotStore) -> None:
        # Act + Assert — must not raise
        await bot_store.delete("telegram", "nonexistent")

        # Assert — cache untouched
        assert bot_store.get_all() == []

    async def test_get_all_returns_all_rows(self, bot_store: BotStore) -> None:
        # Arrange
        await bot_store.upsert(make_bot_row("telegram", "main", "a"))
        await bot_store.upsert(make_bot_row("discord", "main", "b"))

        # Act
        all_bots = bot_store.get_all()

        # Assert
        assert len(all_bots) == 2
        keys = {(b.platform, b.bot_id) for b in all_bots}
        assert ("telegram", "main") in keys
        assert ("discord", "main") in keys


# ---------------------------------------------------------------------------
# TestBotStoreReconnect
# ---------------------------------------------------------------------------


class TestBotStoreReconnect:
    """Cache warm-up on a fresh BotStore instance against an existing DB."""

    async def test_reconnect_warms_cache(self, tmp_path: Path) -> None:
        # Arrange — seed a row using the first store instance, then close it
        db_path = tmp_path / "bots.db"
        store1 = BotStore(db_path=str(db_path))
        await store1.connect()
        await store1.upsert(make_bot_row("telegram", "main", "a"))
        await store1.close()

        # Act — open a NEW store against the same DB and connect
        store2 = BotStore(db_path=str(db_path))
        await store2.connect()
        try:
            result = store2.get("telegram", "main")

            # Assert — cache must have been warmed from DB
            assert result is not None
            assert result.platform == "telegram"
            assert result.bot_id == "main"
            assert result.agent == "a"
        finally:
            await store2.close()


# ---------------------------------------------------------------------------
# TestBotRowConversion
# ---------------------------------------------------------------------------


class TestBotRowConversion:
    """BotRow.from_db_row edge cases."""

    def test_from_db_row_null_defaults(self) -> None:
        # Arrange — tuple with NULL trust and hours
        row = (
            "telegram",
            "main",
            "agent-x",
            None,  # webhook_enabled
            None,  # default_trust
            "[]",  # owner_users_json
            "[]",  # trusted_users_json
            None,  # auto_thread
            None,  # thread_hot_hours
            "2024-01-01T00:00:00+00:00",  # updated_at
        )

        # Act
        bot = BotRow.from_db_row(row)

        # Assert — NULL scalars fall back to canonical defaults (SSoT: bot_models.py)
        assert bot.default_trust == "blocked"
        assert bot.thread_hot_hours == 36
        assert bot.webhook_enabled is False
        assert bot.auto_thread is False  # NULL → bool(None) → False
        assert bot.owner_users == []
        assert bot.trusted_users == []
