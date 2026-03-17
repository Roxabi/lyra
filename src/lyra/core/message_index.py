"""MessageIndex — dedicated session routing index for reply-to resume (#341).

Maps (pool_id, platform_msg_id) → session_id for both user and assistant
messages, enabling O(1) reply-to session resolution.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from .sqlite_base import SqliteStore

log = logging.getLogger(__name__)

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS message_index (
    pool_id          TEXT NOT NULL,
    platform_msg_id  TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    role             TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (pool_id, platform_msg_id)
)
"""

_CREATE_IDX_POOL_CREATED = """\
CREATE INDEX IF NOT EXISTS idx_msgidx_pool_created
    ON message_index(pool_id, created_at)
"""


class MessageIndex(SqliteStore):
    """Async SQLite-backed message-to-session index for reply-to resume."""

    async def connect(self) -> None:
        await self._open_db([_CREATE_TABLE, _CREATE_IDX_POOL_CREATED])

    async def upsert(
        self,
        pool_id: str,
        platform_msg_id: str | None,
        session_id: str,
        role: str,
    ) -> None:
        """Index a message. Skips if platform_msg_id is None (circuit-breaker)."""
        if platform_msg_id is None:
            return
        db = self._require_db()
        await db.execute(
            "INSERT OR IGNORE INTO message_index"
            " (pool_id, platform_msg_id, session_id, role, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (pool_id, str(platform_msg_id), session_id, role, _now_iso()),
        )
        await db.commit()

    async def resolve(self, pool_id: str, platform_msg_id: str) -> str | None:
        """O(1) PK lookup — return session_id or None."""
        db = self._require_db()
        async with db.execute(
            "SELECT session_id FROM message_index"
            " WHERE pool_id = ? AND platform_msg_id = ? LIMIT 1",
            (pool_id, str(platform_msg_id)),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def cleanup_older_than(self, days: int) -> int:
        """Delete entries older than *days*. Returns count of deleted rows."""
        db = self._require_db()
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        cursor = await db.execute(
            "DELETE FROM message_index WHERE created_at < ?",
            (cutoff,),
        )
        await db.commit()
        return cursor.rowcount


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
