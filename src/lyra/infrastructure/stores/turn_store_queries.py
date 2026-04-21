"""Session aggregation queries for TurnStore.

Extracted from turn_store.py (issue #753) — standalone async query functions
that operate on an aiosqlite connection, plus SQL constants for queries.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Query SQL constants
# ---------------------------------------------------------------------------

_SELECT_BY_POOL = """
SELECT id, pool_id, session_id, role, platform, user_id,
       content, message_id, reply_message_id, timestamp, metadata
FROM   conversation_turns
WHERE  pool_id = ?
  AND  user_id = ?
ORDER BY timestamp DESC
LIMIT  ?
"""

_COLS = (
    "id",
    "pool_id",
    "session_id",
    "role",
    "platform",
    "user_id",
    "content",
    "message_id",
    "reply_message_id",
    "timestamp",
    "metadata",
)


async def get_last_session(db: aiosqlite.Connection, pool_id: str) -> str | None:
    """Return the most recent session_id for *pool_id*, or None.

    Queries the pool_sessions table (O(1) via index) instead of scanning
    conversation_turns. Returns a session from creation, not first reply.
    """
    try:
        async with db.execute(
            "SELECT session_id FROM pool_sessions"
            " WHERE pool_id = ?"
            " ORDER BY last_active_at DESC LIMIT 1",
            (pool_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        log.exception("get_last_session failed (pool=%s)", pool_id)
        return None


async def get_session_pool_id(db: aiosqlite.Connection, session_id: str) -> str | None:
    """Return the pool_id for a session, or None if not found.

    Used for scope validation at the NATS trust boundary (#525).
    """
    try:
        async with db.execute(
            "SELECT pool_id FROM pool_sessions WHERE session_id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        log.exception("get_session_pool_id failed (session=%s)", session_id)
        return None


async def get_cli_session(db: aiosqlite.Connection, session_id: str) -> str | None:
    """Return the CLI session ID for a Lyra session, or None."""
    try:
        async with db.execute(
            "SELECT cli_session_id FROM pool_sessions WHERE session_id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        log.exception("get_cli_session failed (session=%s)", session_id)
        return None


async def get_cli_session_by_pool(db: aiosqlite.Connection, pool_id: str) -> str | None:
    """Return CLI session ID for the most recent active session of pool_id.

    Queries non-ended sessions first; falls back to most recent overall.
    Used by resume_and_reset() when an exact Lyra session lookup misses.
    """
    try:
        # Prefer a session that hasn't been explicitly ended (/clear)
        async with db.execute(
            "SELECT cli_session_id FROM pool_sessions"
            " WHERE pool_id = ? AND ended_at IS NULL"
            " ORDER BY last_active_at DESC LIMIT 1",
            (pool_id,),
        ) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            return row[0]
        # Fallback: most recent session regardless of ended_at
        async with db.execute(
            "SELECT cli_session_id FROM pool_sessions"
            " WHERE pool_id = ?"
            " ORDER BY last_active_at DESC LIMIT 1",
            (pool_id,),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        log.exception("get_cli_session_by_pool failed (pool=%s)", pool_id)
        return None


async def get_turns(
    db: aiosqlite.Connection, pool_id: str, user_id: str, limit: int = 50
) -> list[dict]:
    """Return the last *limit* turns for *pool_id* and *user_id*, newest first.

    *limit* is silently capped at 500 to guard against runaway reads.

    Both ``pool_id`` and ``user_id`` must match — prevents cross-user reads.

    Args:
        db: Open aiosqlite connection.
        pool_id: Pool identifier to query.
        user_id: Authenticated user identity — must match the turn owner.
        limit: Maximum number of turns to return (default 50).

    Returns:
        List of dicts with keys matching the ``conversation_turns`` columns.
        The ``metadata`` value is deserialized from JSON.
    """
    limit = min(limit, 500)  # guard against runaway reads
    async with db.execute(_SELECT_BY_POOL, (pool_id, user_id, limit)) as cur:
        rows = await cur.fetchall()
    result = []
    for row in rows:
        d = dict(zip(_COLS, row))
        d["metadata"] = json.loads(d["metadata"] or "{}")
        result.append(d)
    return result


async def backfill_sessions(db: aiosqlite.Connection) -> None:
    """One-time backfill: derive pool_sessions from conversation_turns.

    Uses INSERT OR IGNORE so running twice is safe. Skips empty session_id.
    resume_count defaults to 0 (pre-backfill counts are lost).
    """
    try:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO pool_sessions"
            " (session_id, pool_id, started_at, last_active_at, resume_count)"
            " SELECT session_id, pool_id, MIN(timestamp), MAX(timestamp), 0"
            " FROM conversation_turns"
            " WHERE session_id != ''"
            " GROUP BY pool_id, session_id"
        )
        await db.commit()
        if cursor.rowcount and cursor.rowcount > 0:
            log.info("backfill_sessions: backfilled %d session(s)", cursor.rowcount)
    except Exception:
        log.exception("backfill_sessions failed")
