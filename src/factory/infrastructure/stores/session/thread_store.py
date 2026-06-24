"""ThreadStore — persistent Discord thread ownership (discord.db).

Persists the set of threads owned by each Discord bot across restarts.
Table ``discord_threads`` lives in ``discord.db`` (#417 / S4).

Schema
------
thread_id  TEXT PK  — Discord thread snowflake (string)
bot_id     TEXT     — which Discord bot owns this thread
session_id TEXT     — Claude session UUID (updated after first reply)
pool_id    TEXT     — routing pool_id for session resumption
channel_id TEXT     — parent channel snowflake
guild_id   TEXT     — guild snowflake (NULL for DMs)
created_at TEXT     — ISO-8601 UTC
updated_at TEXT     — ISO-8601 UTC
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from factory.infrastructure.stores.base.sqlite_base import (
    SqliteStore,
    _SQLITE_STORE_ERRORS,
)

log = logging.getLogger(__name__)

# session_id/pool_id columns orphaned by #1777 — dead (additive schema).
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS discord_threads (
    thread_id  TEXT NOT NULL,
    bot_id     TEXT NOT NULL,
    session_id TEXT,
    pool_id    TEXT,
    channel_id TEXT NOT NULL,
    guild_id   TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (thread_id, bot_id)
)
"""


class ThreadStore(SqliteStore):
    """SQLite-backed store for Discord thread ownership.

    One instance is shared across all Discord adapters; each adapter
    filters by its own ``bot_id``.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open connection, enable WAL, create table. Idempotent."""
        await self._open_db(ddl=[_CREATE_TABLE])
        log.info("ThreadStore connected (db=%s)", self._db_path)

    async def close(self) -> None:
        """Close DB connection."""
        await super().close()
        log.info("ThreadStore closed")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_thread_ids(
        self,
        bot_id: str,
        active_since: datetime | None = None,
    ) -> list[str]:
        """Return thread_ids owned by bot_id.

        If active_since is provided, only return threads whose updated_at
        is >= that datetime (UTC).  Use this to load a hot set on startup
        instead of restoring every thread ever created.
        """
        try:
            db = self._require_db()
        except _SQLITE_STORE_ERRORS:
            log.exception("ThreadStore.get_thread_ids: store unavailable")
            return []
        rows: list[str] = []
        try:
            if active_since is not None:
                since_iso = active_since.isoformat()
                async with db.execute(
                    "SELECT thread_id FROM discord_threads "
                    "WHERE bot_id = ? AND updated_at >= ?",
                    (bot_id, since_iso),
                ) as cur:
                    async for row in cur:
                        rows.append(row[0])
            else:
                async with db.execute(
                    "SELECT thread_id FROM discord_threads WHERE bot_id = ?",
                    (bot_id,),
                ) as cur:
                    async for row in cur:
                        rows.append(row[0])
        except _SQLITE_STORE_ERRORS:
            log.exception(
                "ThreadStore.get_thread_ids failed (bot_id=%r)", bot_id, exc_info=True
            )
            return []
        return rows

    async def is_owned(self, thread_id: str, bot_id: str) -> bool:
        """Return True if bot_id owns thread_id (cold-path lazy check).

        On store I/O failure, logs and returns False (degrade: treat as not-owned).
        """
        try:
            db = self._require_db()
            async with db.execute(
                "SELECT 1 FROM discord_threads WHERE thread_id = ? AND bot_id = ? LIMIT 1",
                (thread_id, bot_id),
            ) as cur:
                return await cur.fetchone() is not None
        except _SQLITE_STORE_ERRORS:
            log.warning(
                "ThreadStore.is_owned failed (thread_id=%s bot_id=%r)",
                thread_id,
                bot_id,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def claim(
        self,
        thread_id: str,
        bot_id: str,
        channel_id: str,
        guild_id: str | None = None,
    ) -> None:
        """Record that bot_id owns thread_id (upsert, preserves existing session).

        On store I/O failure, logs and returns without raising — inbound may proceed
        with hot-set only (eventual consistency via later is_owned lookups).
        """
        try:
            db = self._require_db()
            now = datetime.now(UTC).isoformat()
            await db.execute(
                "INSERT INTO discord_threads "
                "(thread_id, bot_id, channel_id, guild_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(thread_id, bot_id) DO UPDATE SET "
                "updated_at=excluded.updated_at",
                (thread_id, bot_id, channel_id, guild_id, now),
            )
            await db.commit()
        except _SQLITE_STORE_ERRORS:
            log.exception(
                "ThreadStore.claim failed (thread_id=%s bot_id=%r)",
                thread_id,
                bot_id,
            )

    async def release(self, thread_id: str, bot_id: str) -> None:
        """Remove thread ownership (e.g. thread archived/deleted)."""
        db = self._require_db()
        await db.execute(
            "DELETE FROM discord_threads WHERE thread_id=? AND bot_id=?",
            (thread_id, bot_id),
        )
        await db.commit()
