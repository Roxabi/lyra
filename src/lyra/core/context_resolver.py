from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedSession:
    session_id: str
    pool_id: str


class ContextResolver:
    """Resolve a Telegram/Discord reply_to message_id to a Claude session_id.

    Queries conversation_turns.reply_message_id (populated by #67).
    Returns None gracefully when DB is absent or table missing.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def resolve(self, reply_to_id: str) -> ResolvedSession | None:
        if not self._db_path.exists():
            return None
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    "SELECT session_id, pool_id FROM conversation_turns"
                    " WHERE reply_message_id = ? LIMIT 1",
                    (reply_to_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row is None:
                        return None
                    return ResolvedSession(session_id=row[0], pool_id=row[1])
        except (sqlite3.Error, OSError):
            log.warning(
                "ContextResolver.resolve failed for reply_to_id=%r",
                reply_to_id,
                exc_info=True,
            )
            return None
