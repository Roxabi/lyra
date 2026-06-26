"""Tests for UserStore — canonical users + platform_identities."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from factory.core.auth.platform_keys import USER_ID_PREFIX
from factory.infrastructure.stores.identity.user_store import UserStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def user_store(tmp_path: Path):
    store = UserStore(db_path=tmp_path / "auth.db")
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestEnsureUser:
    @pytest.mark.asyncio
    async def test_ensure_user_creates_canonical_id(self, user_store: UserStore) -> None:
        user_id = await user_store.ensure_user("tg:user:7377831990")
        assert user_id.startswith(USER_ID_PREFIX)
        assert user_store.resolve_user_id("tg:user:7377831990") == user_id

    @pytest.mark.asyncio
    async def test_ensure_user_is_idempotent(self, user_store: UserStore) -> None:
        first = await user_store.ensure_user("tg:user:1")
        second = await user_store.ensure_user("tg:user:1")
        assert first == second


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------


class TestLinking:
    @pytest.mark.asyncio
    async def test_link_merges_platform_keys(self, user_store: UserStore) -> None:
        await user_store.link_platform_keys("tg:user:1", "dc:user:2")
        expected = frozenset({"tg:user:1", "dc:user:2"})
        assert user_store.resolve_aliases("tg:user:1") == expected
        assert user_store.resolve_aliases("dc:user:2") == expected

    @pytest.mark.asyncio
    async def test_link_three_platforms_flat(self, user_store: UserStore) -> None:
        await user_store.link_platform_keys("tg:user:1", "dc:user:2")
        await user_store.link_platform_keys("tg:user:1", "matrix:user:3")
        expected = frozenset({"tg:user:1", "dc:user:2", "matrix:user:3"})
        assert user_store.resolve_aliases("matrix:user:3") == expected

    @pytest.mark.asyncio
    async def test_unlink_splits_secondary(self, user_store: UserStore) -> None:
        await user_store.link_platform_keys("tg:user:1", "dc:user:2")
        removed = await user_store.unlink_platform_key("dc:user:2")
        assert removed is True
        assert user_store.resolve_aliases("tg:user:1") == frozenset({"tg:user:1"})
        assert user_store.resolve_aliases("dc:user:2") == frozenset({"dc:user:2"})

    @pytest.mark.asyncio
    async def test_resolve_unlinked_singleton(self, user_store: UserStore) -> None:
        assert user_store.resolve_aliases("tg:user:99") == frozenset({"tg:user:99"})


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    @pytest.mark.asyncio
    async def test_migrates_identity_aliases_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "auth.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE identity_aliases "
            "(platform_user_id TEXT PRIMARY KEY, primary_id TEXT NOT NULL, "
            "created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO identity_aliases VALUES ('dc:user:2', 'tg:user:1', datetime('now'))"
        )
        conn.commit()
        conn.close()

        store = UserStore(db_path=db_path)
        await store.connect()
        try:
            assert store.resolve_aliases("dc:user:2") == frozenset(
                {"tg:user:1", "dc:user:2"}
            )
            user_id = store.resolve_user_id("tg:user:1")
            assert user_id is not None
            assert store.resolve_user_id("dc:user:2") == user_id
        finally:
            await store.close()