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
from pathlib import Path
from typing import Literal

import aiosqlite

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


class TurnStore:
    """Async SQLite-backed store for raw conversation turns (L1).

    Uses a dedicated ``turns.db`` file separate from the main vault to avoid
    write contention with MemoryManager's AsyncMemoryDB connection.

    Lifecycle::

        store = TurnStore(vault_dir / "turns.db")
        await store.connect()
        ...
        await store.close()
    """

    def __init__(self, db_path: Path | str) -> None:
        self._path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database connection and apply the v3 schema migration."""
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def _migrate(self) -> None:
        """Create conversation_turns table and indices if they do not exist."""
        db = self._db_or_raise()
        await db.execute(_CREATE_TABLE)
        await db.execute(_CREATE_IDX_SESSION)
        await db.execute(_CREATE_IDX_POOL)
        await db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _db_or_raise(self) -> aiosqlite.Connection:
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
            await db.commit()
        except Exception:
            log.exception(
                "TurnStore.log_turn failed (pool=%s session=%s role=%s)",
                pool_id,
                session_id,
                role,
            )

    async def get_turns(
        self, pool_id: str, user_id: str, limit: int = 50
    ) -> list[dict]:
        """Return the last *limit* turns for *pool_id* and *user_id*, newest first.

        Both ``pool_id`` and ``user_id`` must match — prevents cross-user reads.

        Args:
            pool_id: Pool identifier to query.
            user_id: Authenticated user identity — must match the turn owner.
            limit: Maximum number of turns to return (default 50).

        Returns:
            List of dicts with keys matching the ``conversation_turns`` columns.
            The ``metadata`` value is deserialized from JSON.
        """
        db = self._db_or_raise()
        async with db.execute(_SELECT_BY_POOL, (pool_id, user_id, limit)) as cur:
            rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(zip(_COLS, row))
            d["metadata"] = json.loads(d["metadata"] or "{}")
            result.append(d)
        return result
