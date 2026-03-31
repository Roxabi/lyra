"""IdentityAliasStore — persistent cross-platform identity linking with challenge codes.

Manages two tables:
- ``identity_aliases``: maps secondary platform IDs to a canonical primary ID.
- ``link_challenges``: short-lived one-time codes for cross-platform linking
  (/link command).

Reads are synchronous (in-memory cache). Writes are async (SQLite, write-through).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .sqlite_base import SqliteStore

log = logging.getLogger(__name__)

__all__ = ["IdentityAliasStore"]

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_ALIASES = """
CREATE TABLE IF NOT EXISTS identity_aliases (
    platform_user_id TEXT PRIMARY KEY,
    primary_id       TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_CHALLENGES = """
CREATE TABLE IF NOT EXISTS link_challenges (
    code_hash    TEXT PRIMARY KEY,
    initiator_id TEXT NOT NULL,
    platform     TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT NOT NULL
)
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CODE_ALPHABET = string.ascii_uppercase + string.digits  # A-Z 0-9, 36 chars
_CODE_LENGTH = 6
_DEFAULT_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class IdentityAliasStore(SqliteStore):
    """SQLite-backed identity alias store with write-through in-memory cache.

    ``resolve_aliases()`` is synchronous and reads only from the in-memory
    cache, so it never blocks the event loop. All writes go to SQLite first,
    then update the cache atomically.

    Cache layout:
    - ``_cache``: platform_user_id → primary_id  (secondary → canonical)
    - ``_reverse``: primary_id → set of platform_user_ids  (canonical → all linked)
    """

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        self._cache: dict[str, str] = {}
        self._reverse: dict[str, set[str]] = {}

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create tables, warm cache."""
        await self._open_db(ddl=[_CREATE_ALIASES, _CREATE_CHALLENGES])
        await self._warm_cache()
        log.info("IdentityAliasStore connected (db=%s)", self._db_path)

    async def _warm_cache(self) -> None:
        """Load all identity_aliases rows into _cache and _reverse."""
        db = self._require_db()
        self._cache.clear()
        self._reverse.clear()
        async with db.execute(
            "SELECT platform_user_id, primary_id FROM identity_aliases"
        ) as cur:
            async for row in cur:
                platform_user_id, primary_id = row
                self._cache[platform_user_id] = primary_id
                self._reverse.setdefault(primary_id, set()).add(platform_user_id)

    # ------------------------------------------------------------------
    # Alias resolution (sync — cache only)
    # ------------------------------------------------------------------

    def resolve_aliases(self, platform_id: str) -> frozenset[str]:
        """Return all platform IDs for the same person (sync, no I/O).

        Algorithm:
        1. If platform_id is in _cache (it is a secondary) → look up primary_id,
           then return {primary_id} ∪ _reverse[primary_id].
        2. If platform_id is in _reverse (it is a primary itself) → return
           {platform_id} ∪ _reverse[platform_id].
        3. Neither → return frozenset({platform_id}) (unlinked identity).
        """
        primary_id = self._cache.get(platform_id)
        if primary_id is not None:
            # platform_id is a known secondary; gather all siblings via primary
            siblings = self._reverse.get(primary_id, set())
            return frozenset({primary_id} | siblings)

        if platform_id in self._reverse:
            # platform_id is itself a primary
            return frozenset({platform_id} | self._reverse[platform_id])

        return frozenset({platform_id})

    # ------------------------------------------------------------------
    # Alias mutations (async — DB + write-through cache)
    # ------------------------------------------------------------------

    async def link(self, primary_id: str, secondary_id: str) -> None:
        """Persist an alias and update both cache dicts.

        Maps secondary_id → primary_id. If secondary_id was previously linked
        to a different primary, the old relationship is replaced.
        """
        db = self._require_db()

        # Remove stale cache entry for secondary_id before inserting
        old_primary = self._cache.get(secondary_id)
        if old_primary is not None and old_primary != primary_id:
            self._reverse.get(old_primary, set()).discard(secondary_id)
            if not self._reverse.get(old_primary):
                self._reverse.pop(old_primary, None)

        await db.execute(
            "INSERT INTO identity_aliases (platform_user_id, primary_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(platform_user_id) DO UPDATE "
            "SET primary_id = excluded.primary_id",
            (secondary_id, primary_id),
        )
        await db.commit()

        # Write-through: _cache and _reverse
        self._cache[secondary_id] = primary_id
        self._reverse.setdefault(primary_id, set()).add(secondary_id)

        log.info("Linked %s → %s", secondary_id, primary_id)

    async def unlink(self, platform_id: str) -> bool:
        """Remove an alias for platform_id. Returns True if it existed.

        Removes from _cache and cleans up _reverse (deletes key if set becomes empty).
        Only secondary IDs (those stored as platform_user_id) can be unlinked this way.
        """
        db = self._require_db()

        async with db.execute(
            "DELETE FROM identity_aliases WHERE platform_user_id = ?", (platform_id,)
        ) as cur:
            deleted = cur.rowcount > 0
        await db.commit()

        if deleted:
            primary_id = self._cache.pop(platform_id, None)
            if primary_id is not None:
                siblings = self._reverse.get(primary_id)
                if siblings is not None:
                    siblings.discard(platform_id)
                    if not siblings:
                        del self._reverse[primary_id]

        return deleted

    # ------------------------------------------------------------------
    # Link challenges (/link command)
    # ------------------------------------------------------------------

    async def create_challenge(
        self,
        initiator_id: str,
        platform: str,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> str:
        """Generate a 6-char alphanumeric code, store its SHA-256 hash.

        Returns the plaintext code. Cleans up expired rows before inserting.
        """
        db = self._require_db()

        # Purge expired challenges
        await db.execute(
            "DELETE FROM link_challenges WHERE expires_at < datetime('now')"
        )

        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        code_hash = _sha256(code)
        expires_at = _utc_now() + timedelta(seconds=ttl_seconds)

        await db.execute(
            "INSERT INTO link_challenges "
            "(code_hash, initiator_id, platform, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (code_hash, initiator_id, platform, expires_at.isoformat()),
        )
        await db.commit()

        log.info(
            "Created link challenge for %s on %s (expires %s)",
            initiator_id,
            platform,
            expires_at,
        )
        return code

    async def validate_challenge(self, code: str) -> tuple[bool, str, str]:
        """Validate a link challenge code. Returns (valid, initiator_id, platform).

        Deletes the row on success or if expired. Returns ("", "") for the id/platform
        fields on failure.
        """
        db = self._require_db()
        code_hash = _sha256(code)

        async with db.execute(
            "SELECT initiator_id, platform, expires_at FROM link_challenges "
            "WHERE code_hash = ?",
            (code_hash,),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return False, "", ""

        initiator_id, platform, expires_at_str = row

        # Always delete the row (consumed on first attempt, success or expiry)
        await db.execute(
            "DELETE FROM link_challenges WHERE code_hash = ?", (code_hash,)
        )
        await db.commit()

        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if _utc_now() > expires_at:
            log.debug("Link challenge expired for initiator %s", initiator_id)
            return False, "", ""

        log.info(
            "Validated link challenge for initiator %s on %s", initiator_id, platform
        )
        return True, initiator_id, platform

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the database connection."""
        await super().close()
        log.info("IdentityAliasStore closed")
