"""AuthStore: SQLite + write-through cache for user-level authorization grants."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from lyra.core.trust import TrustLevel

from .sqlite_base import SqliteStore

log = logging.getLogger(__name__)

__all__ = ["AuthStore"]

_PLATFORM_PREFIX: dict[str, str] = {
    "telegram": "tg:user:",
    "discord": "dc:user:",
}


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


class AuthStore(SqliteStore):
    """SQLite-backed authorization store with write-through in-memory cache.

    All user-level grants (pairing + config) are stored here as the single
    source of truth. check() is synchronous and reads only from the cache,
    so it never blocks the event loop.
    """

    def __init__(
        self, db_path: str | Path, default: TrustLevel = TrustLevel.PUBLIC
    ) -> None:
        super().__init__(db_path)
        self._default = default
        self._cache: dict[str, tuple[TrustLevel, datetime | None]] = {}

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create grants table, warm cache."""
        await self._open_db(ddl=[_CREATE_GRANTS])
        await self._warm_cache()
        await self._cleanup_bare_ids()
        log.info("AuthStore connected (db=%s)", self._db_path)

    async def _cleanup_bare_ids(self) -> None:
        """Delete legacy bare-ID grants (no platform prefix). Idempotent."""
        db = self._require_db()
        async with db.execute(
            "DELETE FROM grants WHERE identity_key NOT LIKE '%:%'"
        ) as cur:
            deleted = cur.rowcount
        if deleted:
            await db.commit()
            self._cache = {k: v for k, v in self._cache.items() if ":" in k}
            log.info("Cleaned up %d bare-ID grants", deleted)

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

        Expired grants are eagerly evicted from cache; DB deletion is scheduled
        as a fire-and-forget async task on the running event loop.
        """
        entry = self._cache.get(identity_key)
        if entry is None:
            return self._default
        trust, expires_at = entry
        if expires_at is not None and _utc_now() > expires_at:
            # Eagerly remove from cache
            self._cache.pop(identity_key, None)
            # Schedule async DB delete — non-blocking, best-effort
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.revoke(identity_key))
            except RuntimeError:
                # No running loop (e.g. called from sync test context) — skip eviction
                pass
            return self._default
        return trust

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
            "source=excluded.source "
            "WHERE grants.expires_at IS NOT NULL"
        )
        await db.execute(
            _SQL,
            (identity_key, trust_level.value, exp_iso, granted_by, source),
        )
        await db.commit()
        existing = self._cache.get(identity_key)
        if existing is None or existing[1] is not None:
            self._cache[identity_key] = (trust_level, expires_at)

    async def revoke(self, identity_key: str) -> bool:
        """Delete a grant. Returns True if it existed, False otherwise."""
        db = self._require_db()
        async with db.execute(
            "DELETE FROM grants WHERE identity_key = ?", (identity_key,)
        ) as cur:
            deleted = cur.rowcount > 0
        await db.commit()
        self._cache.pop(identity_key, None)
        return deleted

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

        prefix = _PLATFORM_PREFIX.get(section, "")
        entries: list[tuple[str, TrustLevel]] = []
        for uid in section_cfg.get("owner_users", []):
            entries.append((f"{prefix}{uid}", TrustLevel.OWNER))
        for uid in section_cfg.get("trusted_users", []):
            entries.append((f"{prefix}{uid}", TrustLevel.TRUSTED))

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
        await super().close()
        log.info("AuthStore closed")
