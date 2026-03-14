"""AuthStore: SQLite + write-through cache for user-level authorization grants."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from lyra.core.trust import TrustLevel

log = logging.getLogger(__name__)

__all__ = ["AuthStore"]


_CREATE_GRANTS = """
CREATE TABLE IF NOT EXISTS grants (
    id           INTEGER PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    trust_level  TEXT NOT NULL,
    expires_at   TEXT,
    granted_by   TEXT NOT NULL,
    source       TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuthStore:
    """SQLite-backed authorization store with write-through in-memory cache.

    All user-level grants (pairing + config) are stored here as the single
    source of truth. check() is synchronous and reads only from the cache,
    so it never blocks the event loop.
    """

    def __init__(
        self, db_path: str | Path, default: TrustLevel = TrustLevel.PUBLIC
    ) -> None:
        self._db_path = str(db_path)
        self._default = default
        self._cache: dict[str, tuple[TrustLevel, datetime | None]] = {}
        self._db: aiosqlite.Connection | None = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("call connect() first")
        return self._db

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create grants table, warm cache."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_GRANTS)
        await self._db.commit()
        await self._warm_cache()
        log.info("AuthStore connected (db=%s)", self._db_path)

    async def _warm_cache(self) -> None:
        """Load all non-expired grants from DB into _cache."""
        db = self._require_db()
        now_iso = _utc_now().isoformat()
        self._cache.clear()
        async with db.execute(
            "SELECT identity_key, trust_level, expires_at FROM grants "
            "WHERE expires_at IS NULL OR expires_at > ?",
            (now_iso,),
        ) as cur:
            async for row in cur:
                identity_key, trust_str, expires_at_str = row
                expires_at: datetime | None = None
                if expires_at_str is not None:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                self._cache[identity_key] = (TrustLevel(trust_str), expires_at)

    def check(self, identity_key: str) -> TrustLevel:
        """Return the TrustLevel for identity_key from cache (sync, no I/O).

        Expired grants are eagerly evicted from cache and synchronously removed
        from DB via a brief sqlite3 call (WAL mode makes concurrent access safe).
        """
        entry = self._cache.get(identity_key)
        if entry is None:
            return self._default
        trust, expires_at = entry
        if expires_at is not None and _utc_now() > expires_at:
            # Eagerly remove from cache
            self._cache.pop(identity_key, None)
            # Synchronous DB delete — uses a separate sqlite3 connection so it
            # doesn't race with the aiosqlite connection's async queue
            self._evict_sync(identity_key)
            return self._default
        return trust

    def _evict_sync(self, identity_key: str) -> None:
        """Delete an expired grant from DB synchronously (called from check())."""
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    "DELETE FROM grants WHERE identity_key = ?", (identity_key,)
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            log.debug(
                "Failed to evict expired grant for %s", identity_key, exc_info=True
            )
        log.debug("Evicted expired grant from DB for %s", identity_key)

    async def upsert(
        self,
        identity_key: str,
        trust_level: TrustLevel,
        expires_at: datetime | None,
        granted_by: str,
        source: str,
    ) -> None:
        """Insert or replace a grant in DB and cache."""
        db = self._require_db()
        exp_iso = expires_at.isoformat() if expires_at is not None else None
        _SQL = (
            "INSERT INTO grants "
            "(identity_key, trust_level, expires_at, granted_by, source) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(identity_key) DO UPDATE SET "
            "trust_level=excluded.trust_level, "
            "expires_at=excluded.expires_at, "
            "granted_by=excluded.granted_by, "
            "source=excluded.source"
        )
        await db.execute(
            _SQL,
            (identity_key, trust_level.value, exp_iso, granted_by, source),
        )
        await db.commit()
        self._cache[identity_key] = (trust_level, expires_at)

    async def revoke(self, identity_key: str) -> bool:
        """Delete a grant. Returns True if it existed, False otherwise."""
        db = self._require_db()
        async with db.execute(
            "SELECT id FROM grants WHERE identity_key = ?", (identity_key,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        await db.execute(
            "DELETE FROM grants WHERE identity_key = ?", (identity_key,)
        )
        await db.commit()
        self._cache.pop(identity_key, None)
        return True

    async def seed_from_config(self, raw: dict, section: str) -> None:
        """Seed owner_users and trusted_users from config as permanent grants.

        Permanent grants (expires_at=NULL) are never downgraded — the SQL
        conflict rule only updates rows whose existing expires_at IS NOT NULL.
        The cache is updated only if no permanent grant already exists for the key.
        """
        db = self._require_db()
        auth_block = raw.get("auth", {})
        section_cfg = auth_block.get(section)
        if section_cfg is None:
            return

        entries: list[tuple[str, TrustLevel]] = []
        for uid in section_cfg.get("owner_users", []):
            entries.append((str(uid), TrustLevel.OWNER))
        for uid in section_cfg.get("trusted_users", []):
            entries.append((str(uid), TrustLevel.TRUSTED))

        for identity_key, trust in entries:
            _SEED_SQL = (
                "INSERT INTO grants "
                "(identity_key, trust_level, expires_at, granted_by, source) "
                "VALUES (?, ?, NULL, 'config', 'config.toml') "
                "ON CONFLICT(identity_key) DO UPDATE SET "
                "trust_level=excluded.trust_level, "
                "granted_by='config', "
                "source='config.toml' "
                "WHERE grants.expires_at IS NOT NULL"
            )
            await db.execute(_SEED_SQL, (identity_key, trust.value))
            # Only update cache if there's no existing permanent grant
            existing = self._cache.get(identity_key)
            if existing is None or existing[1] is not None:
                # No cache entry or cache entry is temporary — update cache
                self._cache[identity_key] = (trust, None)

        await db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            log.info("AuthStore closed")
