"""BotStore: SQLite + write-through cache for bot configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from factory.core.agent.agent_models import _utc_now_iso
from factory.core.agent.bot_models import BotRow
from factory.core.agent.schema.bot_schema import (
    _CREATE_BOTS,
    _SELECT_BOTS,
    _UPSERT_BOT,
)
from factory.core.stores.bot_store_protocol import BotStoreProtocol
from factory.infrastructure.stores.base.sqlite_base import (
    _SQLITE_STORE_ERRORS,
    SqliteStore,
)
from factory.infrastructure.stores.migrations.bot_store_migrations import (
    run_bot_migrations,
)

log = logging.getLogger(__name__)

__all__ = ["BotStore", "BotRow"]


class BotStore(SqliteStore, BotStoreProtocol):
    """SQLite-backed bot configuration store with write-through in-memory cache.

    Sync reads (get / get_all) serve from cache and never block the event loop.
    Async writes (upsert / delete) persist to SQLite and update the cache atomically.
    """

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        self._bots: dict[tuple[str, str], BotRow] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create tables, warm cache. Idempotent."""
        if self._db is not None:
            return  # already connected
        await self._open_db(ddl=[_CREATE_BOTS])
        try:
            db = self._require_db()
            await run_bot_migrations(db)
            await self._warm_cache()
        except _SQLITE_STORE_ERRORS:
            log.exception("BotStore.connect() setup failed; closing connection")
            await self.close()
            raise
        log.info("BotStore connected (db=%s)", self._db_path)

    async def _warm_cache(self) -> None:
        """Load bots into in-memory cache."""
        db = self._require_db()
        self._bots.clear()
        async with db.execute(_SELECT_BOTS) as cur:
            async for row in cur:
                bot = BotRow.from_db_row(tuple(row))
                self._bots[(bot.platform, bot.bot_id)] = bot

    async def close(self) -> None:
        """Close the database connection and clear caches."""
        if self._db is not None:
            await super().close()
            self._bots.clear()
            log.info("BotStore closed")

    # ------------------------------------------------------------------
    # Sync reads (cache only)
    # ------------------------------------------------------------------

    def get(self, platform: str, bot_id: str) -> BotRow | None:
        """Return BotRow for (platform, bot_id), or None. Raises if not connected."""
        self._require_db()
        return self._bots.get((platform, bot_id))

    def get_all(self) -> list[BotRow]:
        """Return all cached bots. Raises if not connected."""
        self._require_db()
        return list(self._bots.values())

    # ------------------------------------------------------------------
    # Async writes
    # ------------------------------------------------------------------

    async def upsert(self, row: BotRow) -> None:
        """Insert or update a bot row in DB and cache."""
        db = self._require_db()
        now = _utc_now_iso()
        await db.execute(
            _UPSERT_BOT,
            (
                row.platform,
                row.bot_id,
                row.agent,
                1 if row.webhook_enabled else 0,
                1 if row.auto_thread else 0,
                row.thread_hot_hours,
                now,
                row.public_bot,
            ),
        )
        await db.commit()
        self._bots[(row.platform, row.bot_id)] = BotRow(
            platform=row.platform,
            bot_id=row.bot_id,
            agent=row.agent,
            webhook_enabled=row.webhook_enabled,
            auto_thread=row.auto_thread,
            thread_hot_hours=row.thread_hot_hours,
            updated_at=now,
            public_bot=row.public_bot,
        )

    async def delete(self, platform: str, bot_id: str) -> None:
        """Delete a bot. No-op if it does not exist."""
        db = self._require_db()
        await db.execute(
            "DELETE FROM bots WHERE platform = ? AND bot_id = ?",
            (platform, bot_id),
        )
        await db.commit()
        self._bots.pop((platform, bot_id), None)
