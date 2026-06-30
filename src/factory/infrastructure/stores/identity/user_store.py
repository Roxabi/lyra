"""UserStore — canonical users + platform_identities (replaces flat alias graph)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from factory.core.auth.platform_keys import (
    USER_ID_PREFIX,
    format_platform_key,
    parse_platform_key,
)
from factory.core.auth.user_models import PlatformIdentity, User
from factory.infrastructure.stores.base.sqlite_base import SqliteStore
from factory.infrastructure.stores.identity.user_store_profile import (
    UserStoreProfileOps,
)

log = logging.getLogger(__name__)

__all__ = ["UserStore", "_CREATE_PLATFORM_IDENTITIES", "_CREATE_USERS", "_CREATE_USER_MIGRATION"]  # noqa: E501

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    display_name TEXT,
    email        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_USERS_EMAIL_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email
ON users(email) WHERE email IS NOT NULL
"""

_CREATE_PLATFORM_IDENTITIES = """
CREATE TABLE IF NOT EXISTS platform_identities (
    platform_key TEXT PRIMARY KEY,
    platform     TEXT NOT NULL,
    platform_uid TEXT NOT NULL,
    user_id      TEXT NOT NULL REFERENCES users(id),
    linked_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, platform_uid)
)
"""

_CREATE_USER_MIGRATION = """
CREATE TABLE IF NOT EXISTS _user_store_migration (
    migrated_at TEXT NOT NULL
)
"""

_IDENTITY_COLS = "platform_key, platform, platform_uid, user_id, linked_at"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_user_id() -> str:
    return f"{USER_ID_PREFIX}{uuid4().hex}"


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


class UserStore(UserStoreProfileOps, SqliteStore):
    """SQLite-backed canonical user registry with write-through cache.

    ``resolve_*`` methods are synchronous (cache-only). Writes update SQLite
    then refresh the cache for affected users.
    """

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        self._key_to_user: dict[str, str] = {}
        self._user_to_keys: dict[str, set[str]] = {}

    async def connect(self) -> None:
        await self._open_db(
            ddl=[_CREATE_USERS, _CREATE_PLATFORM_IDENTITIES, _CREATE_USER_MIGRATION]
        )
        await self._migrate_users_email()
        await self._warm_cache()
        await self._migrate_legacy_aliases()
        log.info("UserStore connected (db=%s)", self._db_path)

    async def _migrate_users_email(self) -> None:
        """Add ``email`` column + partial unique index on existing auth.db."""
        db = self._require_db()
        async with db.execute("PRAGMA table_info(users)") as cur:
            cols = {row[1] async for row in cur}
        if "email" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN email TEXT")
            await db.commit()
        await db.execute(_CREATE_USERS_EMAIL_INDEX)
        await db.commit()

    async def _warm_cache(self) -> None:
        db = self._require_db()
        self._key_to_user.clear()
        self._user_to_keys.clear()
        async with db.execute(
            f"SELECT {_IDENTITY_COLS} FROM platform_identities"
        ) as cur:
            async for row in cur:
                platform_key, _, _, user_id, _ = row
                self._key_to_user[platform_key] = user_id
                self._user_to_keys.setdefault(user_id, set()).add(platform_key)

    async def _migrate_legacy_aliases(self) -> None:
        """One-shot import from ``identity_aliases`` (#472 → UserStore)."""
        db = self._require_db()
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_user_store_migration'"  # noqa: E501
        ) as cur:
            if not await cur.fetchone():
                return
        async with db.execute("SELECT 1 FROM _user_store_migration LIMIT 1") as cur:
            if await cur.fetchone():
                return

        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='identity_aliases'"  # noqa: E501
        ) as cur:
            if not await cur.fetchone():
                await db.execute(
                    "INSERT INTO _user_store_migration (migrated_at) VALUES (datetime('now'))"  # noqa: E501
                )
                await db.commit()
                return

        async with db.execute(
            "SELECT platform_user_id, primary_id FROM identity_aliases"
        ) as cur:
            rows = list(await cur.fetchall())

        for secondary_id, primary_id in rows:
            try:
                await self.link_platform_keys(primary_id, secondary_id)
            except ValueError:
                log.warning(
                    "Skipping legacy alias %s → %s (invalid platform key)",
                    secondary_id,
                    primary_id,
                )

        await db.execute(
            "INSERT INTO _user_store_migration (migrated_at) VALUES (datetime('now'))"
        )
        await db.commit()
        if rows:
            log.info("Migrated %d legacy identity_aliases row(s) into UserStore", len(rows))  # noqa: E501

    # ------------------------------------------------------------------
    # Sync resolution (cache only)
    # ------------------------------------------------------------------

    def resolve_user_id(self, platform_key: str) -> str | None:
        return self._key_to_user.get(platform_key)

    def resolve_platform_keys(self, user_id: str) -> frozenset[str]:
        return frozenset(self._user_to_keys.get(user_id, ()))

    def resolve_aliases(self, platform_key: str) -> frozenset[str]:
        """All platform keys for the same person (IdentityAliasStore compat)."""
        user_id = self._key_to_user.get(platform_key)
        if user_id is None:
            return frozenset({platform_key})
        return frozenset(self._user_to_keys.get(user_id, {platform_key}) | {platform_key})  # noqa: E501

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def ensure_user(
        self,
        platform_key: str,
        *,
        display_name: str | None = None,
    ) -> str:
        parsed = parse_platform_key(platform_key)
        if parsed is None:
            raise ValueError(f"Invalid platform key: {platform_key!r}")

        platform, platform_uid, canonical_key = parsed
        existing = self._key_to_user.get(canonical_key)
        if existing is not None:
            return existing

        user_id = _new_user_id()
        db = self._require_db()
        await db.execute(
            "INSERT INTO users (id, display_name, email) VALUES (?, ?, NULL)",
            (user_id, display_name),
        )
        await db.execute(
            "INSERT INTO platform_identities "
            "(platform_key, platform, platform_uid, user_id) "
            "VALUES (?, ?, ?, ?)",
            (canonical_key, platform, platform_uid, user_id),
        )
        await db.commit()

        self._key_to_user[canonical_key] = user_id
        self._user_to_keys.setdefault(user_id, set()).add(canonical_key)
        log.info("Registered user %s for %s", user_id, canonical_key)
        return user_id

    async def link_platform_keys(self, primary_key: str, secondary_key: str) -> str:
        """Merge two platform identities under one canonical user."""
        if primary_key == secondary_key:
            return await self.ensure_user(primary_key)

        primary_user = await self.ensure_user(primary_key)
        secondary_user = await self.ensure_user(secondary_key)

        if primary_user == secondary_user:
            return primary_user

        await self._merge_users(keep_id=primary_user, drop_id=secondary_user)
        return primary_user

    async def _merge_users(self, *, keep_id: str, drop_id: str) -> None:
        db = self._require_db()
        await db.execute(
            "UPDATE platform_identities SET user_id = ? WHERE user_id = ?",
            (keep_id, drop_id),
        )
        await db.execute("DELETE FROM users WHERE id = ?", (drop_id,))
        await db.commit()

        moved = self._user_to_keys.pop(drop_id, set())
        target = self._user_to_keys.setdefault(keep_id, set())
        for key in moved:
            self._key_to_user[key] = keep_id
            target.add(key)

        log.info("Merged user %s into %s (%d identities)", drop_id, keep_id, len(moved))

    async def unlink_platform_key(self, platform_key: str) -> bool:
        """Detach *platform_key* into its own solo user (secondary unlink semantics)."""
        parsed = parse_platform_key(platform_key)
        if parsed is None:
            return False

        _, _, canonical_key = parsed
        old_user_id = self._key_to_user.get(canonical_key)
        if old_user_id is None:
            return False

        siblings = self._user_to_keys.get(old_user_id, set())
        if len(siblings) <= 1:
            return False

        new_user_id = _new_user_id()
        db = self._require_db()
        await db.execute(
            "INSERT INTO users (id, display_name, email) VALUES (?, NULL, NULL)",
            (new_user_id,),
        )
        await db.execute(
            "UPDATE platform_identities SET user_id = ? WHERE platform_key = ?",
            (new_user_id, canonical_key),
        )
        await db.commit()

        siblings.discard(canonical_key)
        self._key_to_user[canonical_key] = new_user_id
        self._user_to_keys[new_user_id] = {canonical_key}
        if not siblings:
            self._user_to_keys.pop(old_user_id, None)
            await db.execute("DELETE FROM users WHERE id = ?", (old_user_id,))
            await db.commit()

        log.info("Unlinked %s from user %s → new user %s", canonical_key, old_user_id, new_user_id)  # noqa: E501
        return True

    async def ensure_from_platform_keys(self, keys: list[str]) -> None:
        """Idempotently register users for known platform identity keys."""
        for key in keys:
            if parse_platform_key(key) is not None:
                await self.ensure_user(key)

    # ------------------------------------------------------------------
    # Reads (async — operator / CLI)
    # ------------------------------------------------------------------

    async def get_user(self, user_id: str) -> User | None:
        db = self._require_db()
        async with db.execute(
            "SELECT id, display_name, email, created_at FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        uid, display_name, email, created_at = row
        return User(
            id=uid,
            display_name=display_name,
            email=email,
            created_at=_parse_ts(created_at),
        )

    async def close(self) -> None:
        await super().close()
        log.info("UserStore closed")