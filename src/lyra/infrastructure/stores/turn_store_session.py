"""TurnStore session management methods — extracted from turn_store.py (issue #760).

Provides a mixin class with session lifecycle methods for TurnStore.
The host class must provide a ``_db_or_raise()`` method returning an
aiosqlite connection.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from lyra.infrastructure.stores.turn_store_queries import (
    get_cli_session,
    get_cli_session_by_pool,
    get_last_session,
    get_session_pool_id,
)

log = logging.getLogger(__name__)

__all__ = ["TurnStoreSessionMixin"]


class TurnStoreSessionMixin:
    """Mixin providing session management methods for TurnStore.

    Requires the host class to provide ``_db_or_raise()`` returning an
    open aiosqlite connection.
    """

    def _db_or_raise(self) -> "aiosqlite.Connection":
        """Return DB connection. Must be overridden by host class."""
        raise NotImplementedError("Host class must provide _db_or_raise")

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
        return await get_last_session(self._db_or_raise(), pool_id)

    async def get_session_pool_id(self, session_id: str) -> str | None:
        """Return the pool_id for a session, or None if not found.

        Used for scope validation at the NATS trust boundary (#525).
        """
        return await get_session_pool_id(self._db_or_raise(), session_id)

    async def set_cli_session(self, session_id: str, cli_session_id: str) -> None:
        """Store the CLI session ID for a Lyra session (for --resume after restart)."""
        db = self._db_or_raise()
        try:
            await db.execute(
                "UPDATE pool_sessions SET cli_session_id = ? WHERE session_id = ?",
                (cli_session_id, session_id),
            )
            await db.commit()
        except Exception:
            log.exception("TurnStore.set_cli_session failed (session=%s)", session_id)
            return

    async def get_cli_session(self, session_id: str) -> str | None:
        """Return the CLI session ID for a Lyra session, or None."""
        return await get_cli_session(self._db_or_raise(), session_id)

    async def get_cli_session_by_pool(self, pool_id: str) -> str | None:
        """Return CLI session ID for the most recent active session of pool_id.

        Queries non-ended sessions first; falls back to most recent overall.
        Used by resume_and_reset() when an exact Lyra session lookup misses.
        """
        return await get_cli_session_by_pool(self._db_or_raise(), pool_id)

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
            return

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
            return
