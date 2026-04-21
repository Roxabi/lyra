"""AuthStore.check() — sync cache lookup, expiry handling, missing key."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lyra.core.auth.trust import TrustLevel
from tests.core.conftest import make_auth_store

# ---------------------------------------------------------------------------
# TestAuthStoreCheck
# ---------------------------------------------------------------------------


class TestAuthStoreCheck:
    """AuthStore.check() — sync cache lookup, expiry handling, missing key."""

    async def test_check_returns_correct_level_after_upsert(
        self, tmp_path: Path
    ) -> None:
        store = await make_auth_store(tmp_path)
        try:
            await store.upsert("alice", TrustLevel.OWNER, None, "config", "config.toml")
            assert store.check("alice") == TrustLevel.OWNER

            await store.upsert("bob", TrustLevel.TRUSTED, None, "invite", "code-hash")
            assert store.check("bob") == TrustLevel.TRUSTED
        finally:
            await store.close()

    async def test_check_missing_key_returns_default(self, tmp_path: Path) -> None:
        store = await make_auth_store(tmp_path)
        try:
            result = store.check("nobody")
            assert result == TrustLevel.PUBLIC
        finally:
            await store.close()

    async def test_expired_grant_returns_default_not_cached_level(
        self, tmp_path: Path
    ) -> None:
        store = await make_auth_store(tmp_path)
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
        store = await make_auth_store(tmp_path)
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
        store = await make_auth_store(tmp_path)
        try:
            future = datetime.now(timezone.utc) + timedelta(days=30)
            await store.upsert(
                "future-user", TrustLevel.TRUSTED, future, "invite", "hash"
            )
            assert store.check("future-user") == TrustLevel.TRUSTED
        finally:
            await store.close()
