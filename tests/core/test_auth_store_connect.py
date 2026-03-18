"""AuthStore.connect() — schema setup, WAL mode, cache warm-up, idempotency."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lyra.core.auth_store import AuthStore
from lyra.core.trust import TrustLevel
from tests.core.conftest import make_auth_store

# ---------------------------------------------------------------------------
# TestAuthStoreConnect
# ---------------------------------------------------------------------------


class TestAuthStoreConnect:
    """AuthStore.connect() — creates grants table with UNIQUE on identity_key,
    WAL mode enabled, and warms cache from DB."""

    async def test_grants_table_created(self, tmp_path: Path) -> None:
        store = await make_auth_store(tmp_path)
        try:
            # Table must exist after connect()
            assert store._db is not None
            async with store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='grants'"
            ) as cur:
                row = await cur.fetchone()
            assert row is not None, "grants table must exist after connect()"
        finally:
            await store.close()

    async def test_identity_key_unique_constraint(self, tmp_path: Path) -> None:
        import aiosqlite

        store = await make_auth_store(tmp_path)
        try:
            now = datetime.now(timezone.utc).isoformat()
            assert store._db is not None
            _INSERT = (
                "INSERT INTO grants"
                " (identity_key, trust_level, granted_by, source, created_at)"
                " VALUES (?, ?, ?, ?, ?)"
            )
            await store._db.execute(
                _INSERT,
                ("user-1", "trusted", "test", "test", now),
            )
            await store._db.commit()
            with pytest.raises(aiosqlite.IntegrityError):
                await store._db.execute(
                    _INSERT,
                    ("user-1", "owner", "test", "test", now),
                )
                await store._db.commit()
        finally:
            await store.close()

    async def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        store = await make_auth_store(tmp_path)
        try:
            assert store._db is not None
            async with store._db.execute("PRAGMA journal_mode") as cur:
                row = await cur.fetchone()
            assert row is not None
            assert row[0].lower() == "wal", f"expected WAL mode, got {row[0]!r}"
        finally:
            await store.close()

    async def test_cache_warmed_on_connect(self, tmp_path: Path) -> None:
        """Grants persisted in DB are loaded into cache on connect()."""
        db_path = str(tmp_path / "grants.db")

        # First session: write a grant directly
        store1 = AuthStore(db_path=db_path)
        await store1.connect()
        await store1.upsert("user-persisted", TrustLevel.TRUSTED, None, "test", "test")
        await store1.close()

        # Second session: connect should warm cache from DB
        store2 = AuthStore(db_path=db_path)
        await store2.connect()
        try:
            # check() is sync and must return from cache without DB I/O
            level = store2.check("user-persisted")
            assert level == TrustLevel.TRUSTED
        finally:
            await store2.close()

    async def test_connect_is_idempotent(self, tmp_path: Path) -> None:
        """Calling connect() twice should be a no-op — same connection reused."""
        store = await make_auth_store(tmp_path)
        try:
            first_db = store._db
            await store.connect()  # second call — should be a no-op
            assert store._db is first_db  # same connection object
        finally:
            await store.close()
