"""SqliteStore — shared lifecycle base class for aiosqlite-backed stores."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

__all__ = ["SqliteStore"]


class SqliteStore:
    """Minimal base class providing aiosqlite connection lifecycle.

    Subclasses override ``connect()`` and call ``_open_db(ddl)`` internally::

        async def connect(self) -> None:
            await self._open_db([_CREATE_TABLE])
            # any extra setup (cache warming, etc.)

    ``_open_db`` is idempotent — calling it when already connected is a no-op.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    def _require_db(self) -> aiosqlite.Connection:
        """Return the open connection or raise RuntimeError."""
        if self._db is None:
            raise RuntimeError("call connect() first")
        return self._db

    async def _open_db(self, ddl: list[str] | None = None) -> None:
        """Open aiosqlite connection, enable WAL, run DDL statements, commit.

        Idempotent — if already connected it returns immediately.
        """
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        for stmt in ddl or []:
            await self._db.execute(stmt)
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
