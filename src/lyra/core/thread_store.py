"""ThreadStore — persistent Discord thread ownership (auth.db).

Persists the set of threads owned by each Discord bot across restarts.
Table ``discord_threads`` lives in ``auth.db`` alongside agent/auth tables.

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
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

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


class ThreadStore:
    """SQLite-backed store for Discord thread ownership.

    One instance is shared across all Discord adapters; each adapter
    filters by its own ``bot_id``.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("call connect() first")
        return self._db

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open connection, enable WAL, create table. Idempotent."""
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_TABLE)
        await self._db.commit()
        log.info("ThreadStore connected (db=%s)", self._db_path)

    async def close(self) -> None:
        """Close DB connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
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
        db = self._require_db()
        rows: list[str] = []
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
                "SELECT thread_id FROM discord_threads WHERE bot_id = ?", (bot_id,)
            ) as cur:
                async for row in cur:
                    rows.append(row[0])
        return rows

    async def is_owned(self, thread_id: str, bot_id: str) -> bool:
        """Return True if bot_id owns thread_id (cold-path lazy check)."""
        db = self._require_db()
        async with db.execute(
            "SELECT 1 FROM discord_threads WHERE thread_id = ? AND bot_id = ? LIMIT 1",
            (thread_id, bot_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def get_session(
        self, thread_id: str, bot_id: str
    ) -> tuple[str | None, str | None]:
        """Return (session_id, pool_id) for (thread_id, bot_id), or (None, None)."""
        db = self._require_db()
        async with db.execute(
            "SELECT session_id, pool_id FROM discord_threads "
            "WHERE thread_id = ? AND bot_id = ?",
            (thread_id, bot_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None, None
        return row[0], row[1]

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
        """Record that bot_id owns thread_id (upsert, preserves existing session)."""
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

    async def update_session(
        self, thread_id: str, bot_id: str, session_id: str, pool_id: str
    ) -> None:
        """Persist Claude session_id and pool_id for a thread (future resumption)."""
        db = self._require_db()
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "UPDATE discord_threads SET session_id=?, pool_id=?, updated_at=? "
            "WHERE thread_id=? AND bot_id=?",
            (session_id, pool_id, now, thread_id, bot_id),
        )
        await db.commit()

    async def release(self, thread_id: str, bot_id: str) -> None:
        """Remove thread ownership (e.g. thread archived/deleted)."""
        db = self._require_db()
        await db.execute(
            "DELETE FROM discord_threads WHERE thread_id=? AND bot_id=?",
            (thread_id, bot_id),
        )
        await db.commit()
