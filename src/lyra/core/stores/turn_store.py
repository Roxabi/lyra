"""TurnStore — raw conversation turn logging (L1 memory layer, issue #67).

Persists every turn (user + assistant) to the ``conversation_turns`` table
in a dedicated ``turns.db`` SQLite database (separate from roxabi-vault to
avoid write contention). Provides an audit trail with platform message IDs,
session context, and a basic query interface.

Schema: v3 migration — creates ``conversation_turns`` table if absent,
then adds missing indices idempotently (safe to call on an existing DB).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Literal

from .sqlite_base import SqliteStore

log = logging.getLogger(__name__)

# Valid role values for conversation turns.
Role = Literal["user", "assistant"]
_VALID_ROLES: frozenset[str] = frozenset({"user", "assistant"})

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS conversation_turns (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_id           TEXT    NOT NULL,
    session_id        TEXT    NOT NULL,
    role              TEXT    NOT NULL,
    platform          TEXT    NOT NULL,
    user_id           TEXT    NOT NULL,
    content           TEXT    NOT NULL,
    message_id        TEXT,
    reply_message_id  TEXT,
    timestamp         TEXT    NOT NULL,
    metadata          TEXT    DEFAULT '{}'
)
"""

_CREATE_IDX_SESSION = """
CREATE INDEX IF NOT EXISTS idx_turns_session
ON conversation_turns(session_id, timestamp)
"""

_CREATE_IDX_POOL = """
CREATE INDEX IF NOT EXISTS idx_turns_pool
ON conversation_turns(pool_id, timestamp)
"""

_CREATE_POOL_SESSIONS = """
CREATE TABLE IF NOT EXISTS pool_sessions (
    session_id     TEXT PRIMARY KEY,
    pool_id        TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    ended_at       TEXT,
    resume_count   INTEGER DEFAULT 0,
    metadata       TEXT DEFAULT '{}'
)
"""

_CREATE_IDX_POOL_SESSIONS = """
CREATE INDEX IF NOT EXISTS idx_pool_sessions_pool
ON pool_sessions(pool_id, last_active_at)
"""

_INSERT = """
INSERT INTO conversation_turns
    (pool_id, session_id, role, platform, user_id,
     content, message_id, reply_message_id, timestamp, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

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


class TurnStore(SqliteStore):
    """Async SQLite-backed store for raw conversation turns (L1).

    Uses a dedicated ``turns.db`` file separate from the main vault to avoid
    write contention with MemoryManager's AsyncMemoryDB connection.

    Lifecycle::

        store = TurnStore(vault_dir / "turns.db")
        await store.connect()
        ...
        await store.close()
    """

    async def connect(self) -> None:
        """Open the database connection and apply the v3 schema migration."""
        await self._open_db()
        db = self._require_db()
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(_CREATE_TABLE)
        await db.execute(_CREATE_IDX_SESSION)
        await db.execute(_CREATE_IDX_POOL)
        await db.commit()
        await db.execute(_CREATE_POOL_SESSIONS)
        await db.execute(_CREATE_IDX_POOL_SESSIONS)
        await db.commit()
        # Gate backfill: skip if pool_sessions already has rows
        async with db.execute("SELECT 1 FROM pool_sessions LIMIT 1") as cur:
            if await cur.fetchone() is None:
                await self._backfill_sessions(db)

    def _db_or_raise(self):
        """Return the open connection or raise with TurnStore-specific message."""
        if self._db is None:
            raise RuntimeError("TurnStore not connected — call await connect() first")
        return self._db

    async def log_turn(  # noqa: PLR0913
        self,
        *,
        pool_id: str,
        session_id: str,
        role: str,
        platform: str,
        user_id: str,
        content: str,
        message_id: str | None = None,
        reply_message_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Persist a single conversation turn.

        Args:
            pool_id: Pool identifier (scope key).
            session_id: UUID of the current session.
            role: ``'user'`` or ``'assistant'`` — enforced by allowlist.
            platform: ``'telegram'``, ``'discord'``, ``'cli'``, etc.
            user_id: Platform-specific user identifier.
            content: Raw message text.
            message_id: Inbound platform message ID (user turns).
            reply_message_id: Outbound platform message ID (assistant turns).
            metadata: Optional JSON-serialisable extras.

        Raises:
            ValueError: If *role* is not ``'user'`` or ``'assistant'``.
        """
        if role not in _VALID_ROLES:
            raise ValueError(
                f"invalid role {role!r} — must be one of {sorted(_VALID_ROLES)}"
            )
        db = self._db_or_raise()
        ts = datetime.now(UTC).isoformat()
        meta_str = json.dumps(metadata or {})
        try:
            await db.execute(
                _INSERT,
                (
                    pool_id,
                    session_id,
                    role,
                    platform,
                    user_id,
                    content,
                    message_id,
                    reply_message_id,
                    ts,
                    meta_str,
                ),
            )
            # Ensure session row exists — idempotent, safe on restart
            await db.execute(
                "INSERT OR IGNORE INTO pool_sessions"
                " (session_id, pool_id, started_at, last_active_at)"
                " VALUES (?, ?, ?, ?)",
                (session_id, pool_id, ts, ts),
            )
            # Update session activity timestamp — tolerant: 0-row OK
            await db.execute(
                "UPDATE pool_sessions SET last_active_at = ? WHERE session_id = ?",
                (ts, session_id),
            )
            await db.commit()
        except Exception:
            log.exception(
                "TurnStore.log_turn failed (pool=%s session=%s role=%s)",
                pool_id,
                session_id,
                role,
            )

    async def start_session(self, session_id: str, pool_id: str) -> None:
        """Register a new session. Uses INSERT OR IGNORE — safe on restart."""
        db = self._db_or_raise()
        ts = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT OR IGNORE INTO pool_sessions"
            " (session_id, pool_id, started_at, last_active_at)"
            " VALUES (?, ?, ?, ?)",
            (session_id, pool_id, ts, ts),
        )
        await db.commit()

    async def get_last_session(self, pool_id: str) -> str | None:
        """Return the most recent session_id for *pool_id*, or None.

        Queries the pool_sessions table (O(1) via index) instead of scanning
        conversation_turns. Returns a session from creation, not first reply.
        """
        db = self._db_or_raise()
        try:
            async with db.execute(
                "SELECT session_id FROM pool_sessions"
                " WHERE pool_id = ?"
                " ORDER BY last_active_at DESC LIMIT 1",
                (pool_id,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None
        except Exception:
            log.exception("TurnStore.get_last_session failed (pool=%s)", pool_id)
            return None

    async def get_session_pool_id(self, session_id: str) -> str | None:
        """Return the pool_id for a session, or None if not found.

        Used for scope validation at the NATS trust boundary (#525).
        """
        db = self._db_or_raise()
        try:
            async with db.execute(
                "SELECT pool_id FROM pool_sessions WHERE session_id = ?",
                (session_id,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None
        except Exception:
            log.exception(
                "TurnStore.get_session_pool_id failed (session=%s)", session_id
            )
            return None

    async def _backfill_sessions(self, db) -> None:
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
                log.info(
                    "TurnStore: backfilled %d session(s)",
                    cursor.rowcount,
                )
        except Exception:
            log.exception("TurnStore._backfill_sessions failed")

    async def increment_resume_count(self, session_id: str) -> None:
        """Increment resume_count for *session_id*. Tolerant: 0-row OK."""
        db = self._db_or_raise()
        try:
            await db.execute(
                "UPDATE pool_sessions"
                " SET resume_count = resume_count + 1"
                " WHERE session_id = ?",
                (session_id,),
            )
            await db.commit()
        except Exception:
            log.exception(
                "TurnStore.increment_resume_count failed (session=%s)", session_id
            )

    async def end_session(self, session_id: str) -> None:
        """Stamp ended_at on *session_id*. No-op if already stamped."""
        db = self._db_or_raise()
        ts = datetime.now(UTC).isoformat()
        try:
            await db.execute(
                "UPDATE pool_sessions SET ended_at = ?"
                " WHERE session_id = ? AND ended_at IS NULL",
                (ts, session_id),
            )
            await db.commit()
        except Exception:
            log.exception("TurnStore.end_session failed (session=%s)", session_id)

    async def get_turns(
        self, pool_id: str, user_id: str, limit: int = 50
    ) -> list[dict]:
        """Return the last *limit* turns for *pool_id* and *user_id*, newest first.

        *limit* is silently capped at 500 to guard against runaway reads.

        Both ``pool_id`` and ``user_id`` must match — prevents cross-user reads.

        Args:
            pool_id: Pool identifier to query.
            user_id: Authenticated user identity — must match the turn owner.
            limit: Maximum number of turns to return (default 50).

        Returns:
            List of dicts with keys matching the ``conversation_turns`` columns.
            The ``metadata`` value is deserialized from JSON.
        """
        limit = min(limit, 500)  # guard against runaway reads
        db = self._db_or_raise()
        async with db.execute(_SELECT_BY_POOL, (pool_id, user_id, limit)) as cur:
            rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(zip(_COLS, row))
            d["metadata"] = json.loads(d["metadata"] or "{}")
            result.append(d)
        return result
