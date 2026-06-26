"""IdentityAliasStore — /link challenges + UserStore-backed cross-platform linking.

Legacy ``identity_aliases`` rows are migrated into ``users`` /
``platform_identities`` on UserStore connect (#472 → UserStore).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from factory.infrastructure.stores.base.sqlite_base import SqliteStore

if TYPE_CHECKING:
    from factory.infrastructure.stores.identity.user_store import UserStore

log = logging.getLogger(__name__)

__all__ = ["IdentityAliasStore", "_CREATE_ALIASES", "_CREATE_CHALLENGES"]

# Retained for one-shot migration reads (UserStore._migrate_legacy_aliases).
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

_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LENGTH = 6
_DEFAULT_TTL_SECONDS = 300


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IdentityAliasStore(SqliteStore):
    """Facade: alias resolution/linking via UserStore; challenges stay here."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        user_store: UserStore | None = None,
    ) -> None:
        super().__init__(db_path)
        self._user_store = user_store
        self._owns_user_store = user_store is None

    @property
    def user_store(self) -> UserStore:
        if self._user_store is None:
            raise RuntimeError("UserStore not wired — call connect() first")
        return self._user_store

    async def connect(self) -> None:
        if self._user_store is None:
            from factory.infrastructure.stores.identity.user_store import UserStore

            self._user_store = UserStore(db_path=self._db_path)
            await self._user_store.connect()
        await self._open_db(ddl=[_CREATE_ALIASES, _CREATE_CHALLENGES])
        log.info("IdentityAliasStore connected (db=%s)", self._db_path)

    def resolve_aliases(self, platform_id: str) -> frozenset[str]:
        return self.user_store.resolve_aliases(platform_id)

    async def link(self, primary_id: str, secondary_id: str) -> None:
        await self.user_store.link_platform_keys(primary_id, secondary_id)

    async def unlink(self, platform_id: str) -> bool:
        return await self.user_store.unlink_platform_key(platform_id)

    async def create_challenge(
        self,
        initiator_id: str,
        platform: str,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> str:
        db = self._require_db()
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
        db = self._require_db()
        code_hash = _sha256(code)
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT initiator_id, platform, expires_at FROM link_challenges "
                "WHERE code_hash = ?",
                (code_hash,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                await db.execute("ROLLBACK")
                return False, "", ""
            initiator_id, platform, expires_at_str = row
            await db.execute(
                "DELETE FROM link_challenges WHERE code_hash = ?", (code_hash,)
            )
            await db.execute("COMMIT")
        except Exception:
            await db.execute("ROLLBACK")
            raise

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

    async def close(self) -> None:
        await super().close()
        if self._owns_user_store and self._user_store is not None:
            await self._user_store.close()
            self._user_store = None
        log.info("IdentityAliasStore closed")