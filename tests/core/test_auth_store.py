"""RED-phase tests for AuthStore (issue #245, S1).

All tests in this file are expected to FAIL with ImportError or
ModuleNotFoundError until lyra.core.auth_store is implemented.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lyra.core.auth import TrustLevel
from lyra.core.auth_store import AuthStore

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


async def make_store(tmp_path: Path) -> AuthStore:
    """Create and connect a real AuthStore backed by a tmp file DB."""
    store = AuthStore(db_path=str(tmp_path / "grants.db"))
    await store.connect()
    return store


# ---------------------------------------------------------------------------
# TestAuthStoreConnect
# ---------------------------------------------------------------------------


class TestAuthStoreConnect:
    """AuthStore.connect() — creates grants table with UNIQUE on identity_key,
    WAL mode enabled, and warms cache from DB."""

    async def test_grants_table_created(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
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

        store = await make_store(tmp_path)
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
        store = await make_store(tmp_path)
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
        store = await make_store(tmp_path)
        try:
            first_db = store._db
            await store.connect()  # second call — should be a no-op
            assert store._db is first_db  # same connection object
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# TestAuthStoreCheck
# ---------------------------------------------------------------------------


class TestAuthStoreCheck:
    """AuthStore.check() — sync cache lookup, expiry handling, missing key."""

    async def test_check_returns_correct_level_after_upsert(
        self, tmp_path: Path
    ) -> None:
        store = await make_store(tmp_path)
        try:
            await store.upsert("alice", TrustLevel.OWNER, None, "config", "config.toml")
            assert store.check("alice") == TrustLevel.OWNER

            await store.upsert("bob", TrustLevel.TRUSTED, None, "invite", "code-hash")
            assert store.check("bob") == TrustLevel.TRUSTED
        finally:
            await store.close()

    async def test_check_missing_key_returns_default(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            result = store.check("nobody")
            assert result == TrustLevel.PUBLIC
        finally:
            await store.close()

    async def test_expired_grant_returns_default_not_cached_level(
        self, tmp_path: Path
    ) -> None:
        store = await make_store(tmp_path)
        try:
            past = datetime.now(timezone.utc) - timedelta(seconds=10)
            await store.upsert(
                "expired-user", TrustLevel.TRUSTED, past, "invite", "code-hash"
            )
            # check() should lazily evict and return default
            result = store.check("expired-user")
            assert result == TrustLevel.PUBLIC, (
                f"expected PUBLIC for expired grant, got {result!r}"
            )
        finally:
            await store.close()

    async def test_expired_grant_evicted_from_db(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            past = datetime.now(timezone.utc) - timedelta(seconds=10)
            await store.upsert(
                "expired-evict", TrustLevel.TRUSTED, past, "invite", "code-hash"
            )
            store.check("expired-evict")  # triggers eviction (schedules async task)
            await asyncio.sleep(0)  # yield to event loop so revoke() task runs
            assert store._db is not None
            async with store._db.execute(
                "SELECT id FROM grants WHERE identity_key = ?", ("expired-evict",)
            ) as cur:
                row = await cur.fetchone()
            assert row is None, "expired grant should be deleted from DB on check()"
        finally:
            await store.close()

    async def test_non_expired_grant_returned(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            future = datetime.now(timezone.utc) + timedelta(days=30)
            await store.upsert(
                "future-user", TrustLevel.TRUSTED, future, "invite", "hash"
            )
            assert store.check("future-user") == TrustLevel.TRUSTED
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# TestAuthStoreUpsertRevoke
# ---------------------------------------------------------------------------


class TestAuthStoreUpsertRevoke:
    """AuthStore.upsert() + AuthStore.revoke() — DB + cache writes."""

    async def test_upsert_writes_to_db_and_cache(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            await store.upsert(
                "carol", TrustLevel.TRUSTED, None, "config", "config.toml"
            )
            # Cache check
            assert store.check("carol") == TrustLevel.TRUSTED
            # DB check
            assert store._db is not None
            async with store._db.execute(
                "SELECT trust_level FROM grants WHERE identity_key = ?", ("carol",)
            ) as cur:
                row = await cur.fetchone()
            assert row is not None
            assert row[0] == TrustLevel.TRUSTED
        finally:
            await store.close()

    async def test_upsert_updates_existing_grant(self, tmp_path: Path) -> None:
        """Upsert updates a temporary grant but never overwrites a permanent one (B3).

        A temporary grant (expires_at set) can be replaced by a new upsert.
        A permanent grant (expires_at=None) is protected: subsequent upserts
        are silently ignored so that pairing flows cannot downgrade an OWNER.
        """
        store = await make_store(tmp_path)
        try:
            # Temporary grant can be replaced
            future = datetime.now(timezone.utc) + timedelta(days=30)
            await store.upsert("dave", TrustLevel.TRUSTED, future, "invite", "hash1")
            await store.upsert(
                "dave", TrustLevel.OWNER, future, "config", "config.toml"
            )
            assert store.check("dave") == TrustLevel.OWNER

            # Permanent grant is protected — second upsert is a no-op
            await store.upsert("perm", TrustLevel.OWNER, None, "config", "config.toml")
            await store.upsert("perm", TrustLevel.TRUSTED, future, "invite", "hash2")
            assert store.check("perm") == TrustLevel.OWNER, (
                "permanent grant must not be downgraded by upsert (B3)"
            )
        finally:
            await store.close()

    async def test_revoke_returns_true_if_existed(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            await store.upsert("eve", TrustLevel.TRUSTED, None, "invite", "hash")
            result = await store.revoke("eve")
            assert result is True
        finally:
            await store.close()

    async def test_revoke_returns_false_if_absent(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            result = await store.revoke("nobody")
            assert result is False
        finally:
            await store.close()

    async def test_revoked_key_no_longer_in_check(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            await store.upsert("frank", TrustLevel.TRUSTED, None, "invite", "hash")
            await store.revoke("frank")
            assert store.check("frank") == TrustLevel.PUBLIC
        finally:
            await store.close()

    async def test_revoke_removes_from_db(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            await store.upsert(
                "grace", TrustLevel.OWNER, None, "config", "config.toml"
            )
            await store.revoke("grace")
            assert store._db is not None
            async with store._db.execute(
                "SELECT id FROM grants WHERE identity_key = ?", ("grace",)
            ) as cur:
                row = await cur.fetchone()
            assert row is None, "revoked grant should be deleted from DB"
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# TestSeedFromConfig
# ---------------------------------------------------------------------------


class TestSeedFromConfig:
    """AuthStore.seed_from_config() — seeds owner/trusted from config, no duplicates."""

    def _raw(
        self,
        section: str,
        owner_users: list[str] | None = None,
        trusted_users: list[str] | None = None,
    ) -> dict:
        return {
            "auth": {
                section: {
                    "owner_users": owner_users or [],
                    "trusted_users": trusted_users or [],
                    "default": "blocked",
                }
            }
        }

    async def test_seeds_owner_users_as_owner(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            raw = self._raw("telegram", owner_users=["owner-1"])
            await store.seed_from_config(raw, "telegram")
            assert store.check("owner-1") == TrustLevel.OWNER
        finally:
            await store.close()

    async def test_seeds_trusted_users_as_trusted(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            raw = self._raw("telegram", trusted_users=["trusted-1"])
            await store.seed_from_config(raw, "telegram")
            assert store.check("trusted-1") == TrustLevel.TRUSTED
        finally:
            await store.close()

    async def test_seed_twice_does_not_duplicate(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            raw = self._raw("telegram", owner_users=["owner-1"])
            await store.seed_from_config(raw, "telegram")
            await store.seed_from_config(raw, "telegram")
            # Only one row should exist
            assert store._db is not None
            async with store._db.execute(
                "SELECT COUNT(*) FROM grants WHERE identity_key = ?", ("owner-1",)
            ) as cur:
                row = await cur.fetchone()
            assert row is not None and row[0] == 1
            # Trust level must be preserved after re-seed
            assert store.check("owner-1") == TrustLevel.OWNER
        finally:
            await store.close()

    async def test_seed_does_not_downgrade_permanent_grant(
        self, tmp_path: Path
    ) -> None:
        """A permanent grant (expires_at=NULL) must not be downgraded on re-seed."""
        store = await make_store(tmp_path)
        try:
            # First seed: user as OWNER
            raw_owner = self._raw("telegram", owner_users=["user-42"])
            await store.seed_from_config(raw_owner, "telegram")
            assert store.check("user-42") == TrustLevel.OWNER

            # Second seed: same user now only in trusted_users
            raw_trusted = self._raw("telegram", trusted_users=["user-42"])
            await store.seed_from_config(raw_trusted, "telegram")

            # Must remain OWNER — permanent grants are never downgraded
            assert store.check("user-42") == TrustLevel.OWNER
        finally:
            await store.close()

    async def test_seed_missing_section_returns_early(self, tmp_path: Path) -> None:
        """seed_from_config() should return early without error if section missing."""
        store = await make_store(tmp_path)
        try:
            # No [auth.telegram] section
            await store.seed_from_config({}, "telegram")
            # Nothing should be in the DB
            assert store._db is not None
            async with store._db.execute("SELECT COUNT(*) FROM grants") as cur:
                row = await cur.fetchone()
            assert row is not None and row[0] == 0
        finally:
            await store.close()

    async def test_seed_sets_permanent_expires_at_null(self, tmp_path: Path) -> None:
        store = await make_store(tmp_path)
        try:
            raw = self._raw("telegram", owner_users=["perm-user"])
            await store.seed_from_config(raw, "telegram")
            assert store._db is not None
            async with store._db.execute(
                "SELECT expires_at FROM grants WHERE identity_key = ?", ("perm-user",)
            ) as cur:
                row = await cur.fetchone()
            assert row is not None
            assert row[0] is None, "config-seeded grants must have expires_at=NULL"
        finally:
            await store.close()
