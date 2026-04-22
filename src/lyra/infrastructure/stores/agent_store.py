"""AgentStore: SQLite + write-through cache for agent configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from lyra.core.agent.agent_models import (
    VALID_AGENT_STATUSES,
    AgentRow,
    AgentRuntimeStateRow,
    BotAgentMapRow,
    _utc_now_iso,
)
from lyra.core.agent.agent_schema import (
    _CREATE_AGENT_RUNTIME_STATE,
    _CREATE_AGENTS,
    _SELECT_AGENTS,
    _UPSERT_AGENT,
)
from lyra.core.agent.agent_seeder import seed_from_toml as _seed_from_toml
from lyra.core.stores.agent_store_migrations import run_agent_migrations

from .bot_agent_map import BotAgentMapStore
from .sqlite_base import SqliteStore

log = logging.getLogger(__name__)

__all__ = ["AgentRow", "AgentStore", "AgentRuntimeStateRow", "BotAgentMapRow"]


class AgentStore(SqliteStore):
    """SQLite-backed agent configuration store with write-through in-memory cache.

    Sync reads (get / get_all / get_bot_agent) serve from cache and never block
    the event loop. Async writes (upsert / delete / set_bot_agent / ...) persist
    to SQLite and update the cache atomically.
    """

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        self._agents: dict[str, AgentRow] = {}
        self._bot_map_store = BotAgentMapStore(db_path)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create tables, warm cache. Idempotent."""
        if self._db is not None:
            return  # already connected
        await self._open_db(ddl=[_CREATE_AGENTS, _CREATE_AGENT_RUNTIME_STATE])
        try:
            # Connect bot map store first - it creates bot_agent_map table
            # needed by migrations that add columns to it
            await self._bot_map_store.connect()
            db = self._require_db()
            await run_agent_migrations(db)
            await self._warm_cache()
        except Exception:
            log.exception("AgentStore.connect() setup failed; closing connection")
            await self.close()
            raise
        log.info("AgentStore connected (db=%s)", self._db_path)

    async def _warm_cache(self) -> None:
        """Load agents into in-memory cache."""
        db = self._require_db()
        self._agents.clear()
        async with db.execute(_SELECT_AGENTS) as cur:
            async for row in cur:
                agent = AgentRow.from_db_row(tuple(row))
                self._agents[agent.name] = agent

    async def close(self) -> None:
        """Close the database connection and clear caches."""
        if self._db is not None:
            await super().close()
            self._agents.clear()
            await self._bot_map_store.close()
            log.info("AgentStore closed")

    # ------------------------------------------------------------------
    # Sync reads (cache only)
    # ------------------------------------------------------------------

    def get(self, name: str) -> AgentRow | None:
        """Return AgentRow for name, or None. Raises if not connected."""
        self._require_db()
        return self._agents.get(name)

    def get_all(self) -> list[AgentRow]:
        """Return all cached agents. Raises if not connected."""
        self._require_db()
        return list(self._agents.values())

    # ------------------------------------------------------------------
    # Bot-agent mapping (delegated to BotAgentMapStore)
    # ------------------------------------------------------------------

    def get_bot_agent(self, platform: str, bot_id: str) -> str | None:
        """Return agent_name for (platform, bot_id), or None."""
        return self._bot_map_store.get_bot_agent(platform, bot_id)

    def get_all_bot_mappings(self) -> dict[tuple[str, str], str]:
        """Return a snapshot of all (platform, bot_id) -> agent_name mappings."""
        return self._bot_map_store.get_all_bot_mappings()

    def get_bot_settings(self, platform: str, bot_id: str) -> dict:
        """Return parsed settings dict for (platform, bot_id), or empty dict."""
        return self._bot_map_store.get_bot_settings(platform, bot_id)

    async def set_bot_agent(
        self,
        platform: str,
        bot_id: str,
        agent_name: str,
        *,
        settings: dict | None = None,
    ) -> None:
        """Upsert a bot -> agent mapping with optional settings."""
        await self._bot_map_store.set_bot_agent(
            platform, bot_id, agent_name, settings=settings
        )

    async def set_bot_settings(
        self, platform: str, bot_id: str, settings: dict
    ) -> None:
        """Update settings_json for an existing bot mapping."""
        await self._bot_map_store.set_bot_settings(platform, bot_id, settings)

    async def remove_bot_agent(self, platform: str, bot_id: str) -> None:
        """Remove a bot -> agent mapping. No-op if it does not exist."""
        await self._bot_map_store.remove_bot_agent(platform, bot_id)

    # ------------------------------------------------------------------
    # Async writes
    # ------------------------------------------------------------------

    async def upsert(self, row: AgentRow) -> None:
        """Insert or update an agent row in DB and cache.

        Note: ``persona_json``, ``voice_json``, and ``patterns_json`` use
        COALESCE in the ON CONFLICT clause — passing None preserves the
        existing DB value.
        """
        db = self._require_db()
        now = _utc_now_iso()
        await db.execute(
            _UPSERT_AGENT,
            (
                row.name,
                row.backend,
                row.model,
                row.max_turns or 0,  # None (unlimited) -> 0 sentinel for NOT NULL
                row.tools_json,
                1 if row.show_intermediate else 0,
                row.smart_routing_json,
                row.plugins_json,
                row.memory_namespace,
                row.cwd,
                row.source,
                row.created_at,
                now,
                1 if row.skip_permissions else 0,
                row.permissions_json,
                row.workspaces_json,
                row.commands_json,
                1 if row.streaming else 0,
                row.persona_json,
                row.voice_json,
                row.fallback_language,
                row.patterns_json,
                row.passthroughs_json,
                1 if row.show_tool_recap else 0,
                # ON CONFLICT updated_at value
                now,
            ),
        )
        await db.commit()
        self._agents[row.name] = AgentRow(
            name=row.name,
            backend=row.backend,
            model=row.model,
            max_turns=row.max_turns,
            tools_json=row.tools_json,
            show_intermediate=row.show_intermediate,
            smart_routing_json=row.smart_routing_json,
            plugins_json=row.plugins_json,
            memory_namespace=row.memory_namespace,
            cwd=row.cwd,
            skip_permissions=row.skip_permissions,
            permissions_json=row.permissions_json,
            workspaces_json=row.workspaces_json,
            commands_json=row.commands_json,
            streaming=row.streaming,
            show_tool_recap=row.show_tool_recap,
            persona_json=row.persona_json,
            voice_json=row.voice_json,
            fallback_language=row.fallback_language,
            patterns_json=row.patterns_json,
            passthroughs_json=row.passthroughs_json,
            source=row.source,
            created_at=row.created_at,
            updated_at=now,
        )

    async def delete(self, name: str) -> None:
        """Delete an agent. Raises ValueError if any bot is still assigned to it."""
        # Check if any bot is assigned to this agent
        bot_mappings = self._bot_map_store.get_all_bot_mappings()
        for (_, _), agent_name in bot_mappings.items():
            if agent_name == name:
                raise ValueError(
                    f"Agent {name!r} is still assigned to one or more bots. "
                    "Run 'lyra agent unassign' first."
                )
        db = self._require_db()
        await db.execute("DELETE FROM agents WHERE name = ?", (name,))
        await db.commit()
        self._agents.pop(name, None)

    # ------------------------------------------------------------------
    # Runtime state (not cached — always reads from DB)
    # ------------------------------------------------------------------

    async def get_all_runtime_states(self) -> dict[str, AgentRuntimeStateRow]:
        """Return all agent_runtime_state rows keyed by agent_name."""
        db = self._require_db()
        result: dict[str, AgentRuntimeStateRow] = {}
        async with db.execute(
            "SELECT agent_name, last_active_at, updated_at, pool_count, status "
            "FROM agent_runtime_state"
        ) as cur:
            async for a, la, up, pc, st in cur:
                result[a] = AgentRuntimeStateRow(
                    agent_name=a,
                    last_active_at=la,
                    updated_at=up,
                    pool_count=pc,
                    status=st,
                )
        return result

    async def set_runtime_state(
        self, agent_name: str, status: str, pool_count: int = 0
    ) -> None:
        """Upsert runtime state for an agent."""
        if status not in VALID_AGENT_STATUSES:
            raise ValueError(
                f"invalid status {status!r} — must be one of "
                f"{sorted(VALID_AGENT_STATUSES)}"
            )
        db = self._require_db()
        now = _utc_now_iso()
        await db.execute(
            "INSERT INTO agent_runtime_state "
            "(agent_name, status, pool_count, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(agent_name) DO UPDATE SET "
            "status=excluded.status, "
            "pool_count=excluded.pool_count, "
            "updated_at=excluded.updated_at",
            (agent_name, status, pool_count, now),
        )
        await db.commit()

    # ------------------------------------------------------------------
    # TOML seeding (delegated to agent_seeder)
    # ------------------------------------------------------------------

    async def seed_from_toml(self, path: Path, *, force: bool = False) -> int:
        """Import agent from TOML. Delegates to :func:`agent_seeder.seed_from_toml`."""
        return await _seed_from_toml(self, path, force=force)
