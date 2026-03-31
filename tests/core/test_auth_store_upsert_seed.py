"""AuthStore.upsert(), AuthStore.revoke(), and AuthStore.seed_from_config()."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from lyra.core.trust import TrustLevel
from tests.core.conftest import make_auth_store

# ---------------------------------------------------------------------------
# TestAuthStoreUpsertRevoke
# ---------------------------------------------------------------------------


class TestAuthStoreUpsertRevoke:
    """AuthStore.upsert() + AuthStore.revoke() — DB + cache writes."""

    async def test_upsert_writes_to_db_and_cache(self, tmp_path: Path) -> None:
        store = await make_auth_store(tmp_path)
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
        store = await make_auth_store(tmp_path)
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
        store = await make_auth_store(tmp_path)
        try:
            await store.upsert("eve", TrustLevel.TRUSTED, None, "invite", "hash")
            result = await store.revoke("eve")
            assert result is True
        finally:
            await store.close()

    async def test_revoke_returns_false_if_absent(self, tmp_path: Path) -> None:
        store = await make_auth_store(tmp_path)
        try:
            result = await store.revoke("nobody")
            assert result is False
        finally:
            await store.close()

    async def test_revoked_key_no_longer_in_check(self, tmp_path: Path) -> None:
        store = await make_auth_store(tmp_path)
        try:
            await store.upsert("frank", TrustLevel.TRUSTED, None, "invite", "hash")
            await store.revoke("frank")
            assert store.check("frank") == TrustLevel.PUBLIC
        finally:
            await store.close()

    async def test_revoke_removes_from_db(self, tmp_path: Path) -> None:
        store = await make_auth_store(tmp_path)
        try:
            await store.upsert("grace", TrustLevel.OWNER, None, "config", "config.toml")
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
        store = await make_auth_store(tmp_path)
        try:
            raw = self._raw("telegram", owner_users=["owner-1"])
            await store.seed_from_config(raw, "telegram")
            assert store.check("tg:user:owner-1") == TrustLevel.OWNER
        finally:
            await store.close()

    async def test_seeds_trusted_users_as_trusted(self, tmp_path: Path) -> None:
        store = await make_auth_store(tmp_path)
        try:
            raw = self._raw("telegram", trusted_users=["trusted-1"])
            await store.seed_from_config(raw, "telegram")
            assert store.check("tg:user:trusted-1") == TrustLevel.TRUSTED
        finally:
            await store.close()

    async def test_seed_twice_does_not_duplicate(self, tmp_path: Path) -> None:
        store = await make_auth_store(tmp_path)
        try:
            raw = self._raw("telegram", owner_users=["owner-1"])
            await store.seed_from_config(raw, "telegram")
            await store.seed_from_config(raw, "telegram")
            # Only one row should exist
            assert store._db is not None
            async with store._db.execute(
                "SELECT COUNT(*) FROM grants WHERE identity_key = ?",
                ("tg:user:owner-1",),
            ) as cur:
                row = await cur.fetchone()
            assert row is not None and row[0] == 1
            # Trust level must be preserved after re-seed
            assert store.check("tg:user:owner-1") == TrustLevel.OWNER
        finally:
            await store.close()

    async def test_seed_does_not_downgrade_permanent_grant(
        self, tmp_path: Path
    ) -> None:
        """A permanent grant (expires_at=NULL) must not be downgraded on re-seed."""
        store = await make_auth_store(tmp_path)
        try:
            # First seed: user as OWNER
            raw_owner = self._raw("telegram", owner_users=["user-42"])
            await store.seed_from_config(raw_owner, "telegram")
            assert store.check("tg:user:user-42") == TrustLevel.OWNER

            # Second seed: same user now only in trusted_users
            raw_trusted = self._raw("telegram", trusted_users=["user-42"])
            await store.seed_from_config(raw_trusted, "telegram")

            # Must remain OWNER — permanent grants are never downgraded
            assert store.check("tg:user:user-42") == TrustLevel.OWNER
        finally:
            await store.close()

    async def test_seed_missing_section_returns_early(self, tmp_path: Path) -> None:
        """seed_from_config() should return early without error if section missing."""
        store = await make_auth_store(tmp_path)
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
        store = await make_auth_store(tmp_path)
        try:
            raw = self._raw("telegram", owner_users=["perm-user"])
            await store.seed_from_config(raw, "telegram")
            assert store._db is not None
            async with store._db.execute(
                "SELECT expires_at FROM grants WHERE identity_key = ?",
                ("tg:user:perm-user",),
            ) as cur:
                row = await cur.fetchone()
            assert row is not None
            assert row[0] is None, "config-seeded grants must have expires_at=NULL"
        finally:
            await store.close()
