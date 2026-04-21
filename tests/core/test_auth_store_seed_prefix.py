"""Tests for AuthStore seed prefixes and bare-ID cleanup (#472)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.infrastructure.stores.auth_store import AuthStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def auth_store(tmp_path: Path):
    store = AuthStore(db_path=tmp_path / "grants.db")
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Seed prefix tests
# ---------------------------------------------------------------------------


class TestSeedPrefixes:
    """seed_from_config() must apply platform prefixes to all stored IDs."""

    @pytest.mark.asyncio
    async def test_seed_prefixes_telegram_ids(self, tmp_path: Path) -> None:
        """Seeding with section='telegram' stores IDs as 'tg:user:<id>'."""
        store = AuthStore(db_path=tmp_path / "grants.db")
        await store.connect()
        try:
            raw = {"auth": {"telegram": {"owner_users": ["123"], "trusted_users": []}}}
            await store.seed_from_config(raw, "telegram")

            assert store.check("tg:user:123") == TrustLevel.OWNER
            # Bare ID must not be stored
            assert store.check("123") != TrustLevel.OWNER
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_seed_prefixes_discord_ids(self, tmp_path: Path) -> None:
        """Seeding with section='discord' stores IDs as 'dc:user:<id>'."""
        store = AuthStore(db_path=tmp_path / "grants.db")
        await store.connect()
        try:
            raw = {"auth": {"discord": {"owner_users": [], "trusted_users": ["456"]}}}
            await store.seed_from_config(raw, "discord")

            assert store.check("dc:user:456") == TrustLevel.TRUSTED
            assert store.check("456") != TrustLevel.TRUSTED
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanupBareIds:
    """connect() must call _cleanup_bare_ids() removing legacy bare-ID grants."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_bare_ids(self, tmp_path: Path) -> None:
        """Bare IDs without ':' are removed on connect()."""
        db_path = tmp_path / "grants.db"

        # Write a bare ID directly via the first store
        store1 = AuthStore(db_path=db_path)
        await store1.connect()
        # Insert bare ID grant bypassing normal API (insert raw SQL via upsert)

        await store1.upsert(
            identity_key="123",
            trust_level=TrustLevel.OWNER,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await store1.close()

        # Reconnect — cleanup should fire
        store2 = AuthStore(db_path=db_path)
        await store2.connect()
        try:
            # The bare ID must be gone from cache
            assert store2.check("123") != TrustLevel.OWNER

            # Verify also removed from DB
            db = store2._require_db()
            async with db.execute(
                "SELECT COUNT(*) FROM grants WHERE identity_key = '123'"
            ) as cur:
                row = await cur.fetchone()
            assert row is not None
            assert row[0] == 0
        finally:
            await store2.close()

    @pytest.mark.asyncio
    async def test_cleanup_preserves_prefixed_ids(self, tmp_path: Path) -> None:
        """Prefixed IDs like 'tg:user:123' survive the cleanup pass."""
        db_path = tmp_path / "grants.db"

        store1 = AuthStore(db_path=db_path)
        await store1.connect()
        await store1.upsert(
            identity_key="tg:user:123",
            trust_level=TrustLevel.OWNER,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await store1.close()

        store2 = AuthStore(db_path=db_path)
        await store2.connect()
        try:
            assert store2.check("tg:user:123") == TrustLevel.OWNER
        finally:
            await store2.close()

    @pytest.mark.asyncio
    async def test_resolve_returns_owner_after_seed(self, tmp_path: Path) -> None:
        """After seed_from_config + cleanup, check('tg:user:X') returns OWNER."""
        db_path = tmp_path / "grants.db"

        store = AuthStore(db_path=db_path)
        await store.connect()
        try:
            raw = {"auth": {"telegram": {"owner_users": ["789"], "trusted_users": []}}}
            await store.seed_from_config(raw, "telegram")

            # Reconnect to trigger _cleanup_bare_ids
            await store.close()
            store = AuthStore(db_path=db_path)
            await store.connect()

            assert store.check("tg:user:789") == TrustLevel.OWNER
        finally:
            await store.close()
