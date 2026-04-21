"""BotAgentMapStore: SQLite + write-through cache for bot->agent mappings."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from lyra.core.agent.agent_models import _utc_now_iso
from lyra.core.agent.agent_schema import _CREATE_BOT_AGENT_MAP

from .sqlite_base import SqliteStore

log = logging.getLogger(__name__)

__all__ = ["BotAgentMapStore"]


class BotAgentMapStore(SqliteStore):
    """SQLite-backed bot->agent mapping store with write-through in-memory cache.

    Sync reads (get_bot_agent / get_all_bot_mappings / get_bot_settings) serve
    from cache and never block the event loop. Async writes (set_bot_agent /
    set_bot_settings / remove_bot_agent) persist to SQLite and update the cache
    atomically.
    """

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        self._bot_map: dict[tuple[str, str], str] = {}
        self._bot_settings: dict[tuple[str, str], dict] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create table, warm cache. Idempotent."""
        if self._db is not None:
            return  # already connected
        await self._open_db(ddl=[_CREATE_BOT_AGENT_MAP])
        try:
            await self._warm_cache()
        except Exception:
            log.exception("BotAgentMapStore.connect() setup failed; closing connection")
            await self.close()
            raise
        log.info("BotAgentMapStore connected (db=%s)", self._db_path)

    async def _warm_cache(self) -> None:
        """Load bot_agent_map into in-memory cache."""
        db = self._require_db()
        self._bot_map.clear()
        self._bot_settings.clear()
        async with db.execute(
            "SELECT platform, bot_id, agent_name, settings_json FROM bot_agent_map"
        ) as cur:
            async for row in cur:
                platform, bot_id, agent_name, settings_raw = row
                self._bot_map[(platform, bot_id)] = agent_name
                if settings_raw:
                    try:
                        self._bot_settings[(platform, bot_id)] = json.loads(
                            settings_raw
                        )
                    except json.JSONDecodeError:
                        log.warning(
                            "corrupt settings_json for (%s, %s)", platform, bot_id
                        )

    async def close(self) -> None:
        """Close the database connection and clear caches."""
        if self._db is not None:
            await super().close()
            self._bot_map.clear()
            self._bot_settings.clear()
            log.info("BotAgentMapStore closed")

    # ------------------------------------------------------------------
    # Sync reads (cache only)
    # ------------------------------------------------------------------

    def get_bot_agent(self, platform: str, bot_id: str) -> str | None:
        """Return agent_name for (platform, bot_id), or None."""
        self._require_db()
        return self._bot_map.get((platform, bot_id))

    def get_all_bot_mappings(self) -> dict[tuple[str, str], str]:
        """Return a snapshot of all (platform, bot_id) -> agent_name mappings."""
        self._require_db()
        return dict(self._bot_map)

    def get_bot_settings(self, platform: str, bot_id: str) -> dict:
        """Return parsed settings dict for (platform, bot_id), or empty dict."""
        self._require_db()
        return self._bot_settings.get((platform, bot_id), {})

    # ------------------------------------------------------------------
    # Async writes
    # ------------------------------------------------------------------

    async def set_bot_agent(
        self,
        platform: str,
        bot_id: str,
        agent_name: str,
        *,
        settings: dict | None = None,
    ) -> None:
        """Upsert a bot -> agent mapping with optional settings.

        ``settings=None`` preserves the existing ``settings_json`` value in the
        DB via COALESCE -- it does **not** clear it.  Pass an explicit dict to
        overwrite, or call :meth:`set_bot_settings` to update settings alone.
        """
        db = self._require_db()
        now = _utc_now_iso()
        settings_raw = json.dumps(settings) if settings else None
        await db.execute(
            "INSERT INTO bot_agent_map "
            "(platform, bot_id, agent_name, settings_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(platform, bot_id) DO UPDATE SET "
            "agent_name=excluded.agent_name, "
            "settings_json=COALESCE(excluded.settings_json, "
            "bot_agent_map.settings_json), "
            "updated_at=excluded.updated_at",
            (platform, bot_id, agent_name, settings_raw, now),
        )
        await db.commit()
        self._bot_map[(platform, bot_id)] = agent_name
        if settings is not None:
            self._bot_settings[(platform, bot_id)] = settings

    async def set_bot_settings(
        self, platform: str, bot_id: str, settings: dict
    ) -> None:
        """Update settings_json for an existing bot mapping."""
        db = self._require_db()
        now = _utc_now_iso()
        settings_raw = json.dumps(settings)
        cursor = await db.execute(
            "UPDATE bot_agent_map SET settings_json=?, updated_at=? "
            "WHERE platform=? AND bot_id=?",
            (settings_raw, now, platform, bot_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"No bot_agent_map row for platform={platform!r}, bot_id={bot_id!r}. "
                "Call set_bot_agent() first."
            )
        await db.commit()
        self._bot_settings[(platform, bot_id)] = settings

    async def remove_bot_agent(self, platform: str, bot_id: str) -> None:
        """Remove a bot -> agent mapping. No-op if it does not exist."""
        db = self._require_db()
        await db.execute(
            "DELETE FROM bot_agent_map WHERE platform = ? AND bot_id = ?",
            (platform, bot_id),
        )
        await db.commit()
        self._bot_map.pop((platform, bot_id), None)
        self._bot_settings.pop((platform, bot_id), None)
