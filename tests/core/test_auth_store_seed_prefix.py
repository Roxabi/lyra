"""Tests for AuthStore bare-ID cleanup on connect (#472)."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.core.auth.trust import TrustLevel
from factory.infrastructure.stores.identity.auth_store import AuthStore


@pytest.fixture
async def auth_store(tmp_path: Path):
    store = AuthStore(db_path=tmp_path / "grants.db")
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


class TestCleanupBareIds:
    """connect() must call _cleanup_bare_ids() removing legacy bare-ID grants."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_bare_ids(self, tmp_path: Path) -> None:
        """Bare IDs without ':' are removed on connect()."""
        db_path = tmp_path / "grants.db"

        store1 = AuthStore(db_path=db_path)
        await store1.connect()
        await store1.upsert(
            identity_key="123",
            trust_level=TrustLevel.OWNER,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await store1.close()

        store2 = AuthStore(db_path=db_path)
        await store2.connect()
        try:
            assert store2.check("123") != TrustLevel.OWNER

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
    async def test_prefixed_grant_survives_reconnect(self, tmp_path: Path) -> None:
        """After upsert + reconnect, check('tg:user:X') returns stored level."""
        db_path = tmp_path / "grants.db"

        store = AuthStore(db_path=db_path)
        await store.connect()
        try:
            await store.upsert(
                identity_key="tg:user:789",
                trust_level=TrustLevel.OWNER,
                expires_at=None,
                granted_by="test",
                source="test",
            )
            await store.close()
            store = AuthStore(db_path=db_path)
            await store.connect()
            assert store.check("tg:user:789") == TrustLevel.OWNER
        finally:
            await store.close()