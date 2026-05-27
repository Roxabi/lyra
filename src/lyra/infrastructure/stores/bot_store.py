"""BotStore: SQLite + write-through cache for bot configuration."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from lyra.core.agent.bot_models import BotRow, _utc_now_iso
from lyra.core.stores.bot_store_protocol import BotStoreProtocol
from lyra.infrastructure.stores.bot_store_migrations import run_bot_migrations

from .sqlite_base import SqliteStore

log = logging.getLogger(__name__)

__all__ = ["BotStore", "BotRow"]

_CREATE_BOTS = """
CREATE TABLE IF NOT EXISTS bots (
    platform TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    webhook_enabled INTEGER NOT NULL DEFAULT 0,
    default_trust TEXT NOT NULL DEFAULT 'blocked',
    owner_users_json TEXT NOT NULL DEFAULT '[]',
    trusted_users_json TEXT NOT NULL DEFAULT '[]',
    auto_thread INTEGER NOT NULL DEFAULT 1,
    thread_hot_hours INTEGER NOT NULL DEFAULT 36,
    updated_at TEXT,
    PRIMARY KEY (platform, bot_id)
)
"""

_SELECT_BOTS = (
    "SELECT platform, bot_id, agent, webhook_enabled, default_trust, "
    "owner_users_json, trusted_users_json, auto_thread, thread_hot_hours, updated_at "
    "FROM bots"
)

_N_BOT_COLS = 10

_UPSERT_BOT = (
    f"INSERT INTO bots (platform, bot_id, agent, webhook_enabled, default_trust, "
    f"owner_users_json, trusted_users_json, auto_thread, thread_hot_hours, updated_at) "
    f"VALUES ({', '.join(['?'] * _N_BOT_COLS)}) "
    "ON CONFLICT(platform, bot_id) DO UPDATE SET "
    "agent=excluded.agent, "
    "webhook_enabled=excluded.webhook_enabled, "
    "default_trust=excluded.default_trust, "
    "owner_users_json=excluded.owner_users_json, "
    "trusted_users_json=excluded.trusted_users_json, "
    "auto_thread=excluded.auto_thread, "
    "thread_hot_hours=excluded.thread_hot_hours, "
    "updated_at=excluded.updated_at"
)


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
        except Exception:
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
                row.default_trust,
                json.dumps(row.owner_users),
                json.dumps(row.trusted_users),
                1 if row.auto_thread else 0,
                row.thread_hot_hours,
                now,
            ),
        )
        await db.commit()
        self._bots[(row.platform, row.bot_id)] = BotRow(
            platform=row.platform,
            bot_id=row.bot_id,
            agent=row.agent,
            webhook_enabled=row.webhook_enabled,
            default_trust=row.default_trust,
            owner_users=list(row.owner_users),
            trusted_users=list(row.trusted_users),
            auto_thread=row.auto_thread,
            thread_hot_hours=row.thread_hot_hours,
            updated_at=now,
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
