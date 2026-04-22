"""SqliteStore — shared lifecycle base class for aiosqlite-backed stores."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import weakref
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

__all__ = ["SqliteStore", "close_all_sqlite_stores"]

#: Weak set of open SqliteStore instances for cleanup during test teardown.
#: Weak references allow stores to be garbage-collected normally; the set only
#: prevents the _cleanup fixture from missing stores that are still referenced.
_open_stores: weakref.WeakSet[SqliteStore] = weakref.WeakSet()


async def close_all_sqlite_stores() -> None:
    """Close all tracked SqliteStore instances.

    Called by pytest fixture during event loop teardown to prevent aiosqlite
    thread-blocking issues when the loop closes before connections.
    """
    # Copy to avoid modification during iteration
    stores = list(_open_stores)
    for store in stores:
        try:
            if store._db is not None:
                await store.close()
        except Exception:
            log.debug("Error closing SqliteStore during cleanup", exc_info=True)


class SqliteStore:
    """Minimal base class providing aiosqlite connection lifecycle.

    Subclasses override ``connect()`` and call ``_open_db(ddl)`` internally::

        async def connect(self) -> None:
            await self._open_db([_CREATE_TABLE])
            # any extra setup (cache warming, etc.)

    ``_open_db`` is idempotent — calling it when already connected is a no-op.

    WAL management
    --------------
    SQLite WAL mode is enabled on every connection.  Without periodic
    checkpointing the WAL file grows unbounded when long-lived readers prevent
    SQLite's automatic checkpoint from running.

    This class handles checkpointing in two ways:

    * **On close** — ``close()`` runs ``PRAGMA wal_checkpoint(TRUNCATE)`` before
      closing the connection so the WAL is flushed on every clean shutdown.
    * **Periodic** — a background ``asyncio.Task`` runs the same pragma every
      ``_wal_checkpoint_interval`` seconds (default 30 min) while the store is
      open.  This keeps the WAL bounded during long-running processes.
    """

    #: Seconds between periodic WAL checkpoints.  Override in subclass to tune.
    _wal_checkpoint_interval: int = 1800

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._checkpoint_task: asyncio.Task[None] | None = None

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
        _open_stores.add(self)  # track for pytest cleanup
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=30000")
        for stmt in ddl or []:
            await self._db.execute(stmt)
        await self._db.commit()
        self._checkpoint_task = asyncio.get_running_loop().create_task(
            self._run_periodic_checkpoint(),
            name=f"wal-checkpoint:{self._db_path}",
        )

    async def _checkpoint(self) -> None:
        """Run ``PRAGMA wal_checkpoint(TRUNCATE)`` and log the result.

        Best-effort — logs and returns silently on ``OperationalError`` or
        ``ProgrammingError`` (e.g. active transaction, concurrent readers, or
        connection closed during shutdown).  The WAL will be checkpointed on
        the next opportunity.
        """
        db = self._db
        if db is None:
            return
        try:
            async with db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cur:
                row = await cur.fetchone()
        except (sqlite3.OperationalError, sqlite3.ProgrammingError) as exc:
            log.debug("WAL checkpoint skipped (db=%s): %s", self._db_path, exc)
            return
        if row is not None:
            busy, log_pages, checkpointed = row
            log.debug(
                "WAL checkpoint: db=%s busy=%s log=%s checkpointed=%s",
                self._db_path,
                busy,
                log_pages,
                checkpointed,
            )

    async def _run_periodic_checkpoint(self) -> None:
        """Background task: checkpoint WAL every ``_wal_checkpoint_interval`` s."""
        try:
            while True:
                await asyncio.sleep(self._wal_checkpoint_interval)
                await self._checkpoint()
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        """Checkpoint WAL and close the database connection."""
        if self._checkpoint_task is not None:
            self._checkpoint_task.cancel()
            try:
                await self._checkpoint_task
            except asyncio.CancelledError:
                pass
            self._checkpoint_task = None
        if self._db is not None:
            await self._checkpoint()
            await self._db.close()
            self._db = None
