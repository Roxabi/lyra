"""AuthStore.upsert() and AuthStore.revoke()."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory.core.auth.trust import TrustLevel
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


