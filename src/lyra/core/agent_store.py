"""AgentStore: SQLite + write-through cache for agent configuration."""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

__all__ = ["AgentRow", "AgentStore", "AgentRuntimeStateRow", "BotAgentMapRow"]


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_AGENTS = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    max_turns INTEGER NOT NULL DEFAULT 10,
    tools_json TEXT NOT NULL DEFAULT '[]',
    persona TEXT,
    show_intermediate INTEGER NOT NULL DEFAULT 0,
    smart_routing_json TEXT,
    plugins_json TEXT NOT NULL DEFAULT '[]',
    memory_namespace TEXT,
    cwd TEXT,
    source TEXT NOT NULL DEFAULT 'db',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_BOT_AGENT_MAP = """
CREATE TABLE IF NOT EXISTS bot_agent_map (
    platform TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, bot_id)
)
"""

_CREATE_AGENT_RUNTIME_STATE = """
CREATE TABLE IF NOT EXISTS agent_runtime_state (
    agent_name TEXT PRIMARY KEY,
    last_active_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    pool_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'idle'
)
"""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentRow:
    """One row from the agents table."""

    name: str
    backend: str
    model: str
    max_turns: int = 10
    tools_json: str = "[]"
    persona: str | None = None
    show_intermediate: bool = False
    smart_routing_json: str | None = None
    plugins_json: str = "[]"
    memory_namespace: str | None = None
    cwd: str | None = None
    source: str = "db"
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)


@dataclass
class BotAgentMapRow:
    """One row from the bot_agent_map table."""

    platform: str
    bot_id: str
    agent_name: str
    updated_at: str = field(default_factory=_utc_now_iso)


@dataclass
class AgentRuntimeStateRow:
    """One row from the agent_runtime_state table."""

    agent_name: str
    last_active_at: str | None
    updated_at: str
    pool_count: int
    status: str


# ---------------------------------------------------------------------------
# AgentStore
# ---------------------------------------------------------------------------


class AgentStore:
    """SQLite-backed agent configuration store with write-through in-memory cache.

    Sync reads (get / get_all / get_bot_agent) serve from cache and never block
    the event loop. Async writes (upsert / delete / set_bot_agent / ...) persist
    to SQLite and update the cache atomically.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._agents: dict[str, AgentRow] = {}
        self._bot_map: dict[tuple[str, str], str] = {}

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("call connect() first")
        return self._db

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create tables, warm cache. Idempotent."""
        if self._db is not None:
            return  # already connected
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_AGENTS)
        await self._db.execute(_CREATE_BOT_AGENT_MAP)
        await self._db.execute(_CREATE_AGENT_RUNTIME_STATE)
        await self._db.commit()
        await self._warm_cache()
        log.info("AgentStore connected (db=%s)", self._db_path)

    async def _warm_cache(self) -> None:
        """Load agents and bot_agent_map into in-memory cache."""
        db = self._require_db()
        self._agents.clear()
        async with db.execute(
            "SELECT name, backend, model, max_turns, tools_json, persona, "
            "show_intermediate, smart_routing_json, plugins_json, "
            "memory_namespace, cwd, source, created_at, updated_at FROM agents"
        ) as cur:
            async for row in cur:
                (
                    name,
                    backend,
                    model,
                    max_turns,
                    tools_json,
                    persona,
                    show_intermediate,
                    smart_routing_json,
                    plugins_json,
                    memory_namespace,
                    cwd,
                    source,
                    created_at,
                    updated_at,
                ) = row
                self._agents[name] = AgentRow(
                    name=name,
                    backend=backend,
                    model=model,
                    max_turns=max_turns,
                    tools_json=tools_json,
                    persona=persona,
                    show_intermediate=bool(show_intermediate),
                    smart_routing_json=smart_routing_json,
                    plugins_json=plugins_json,
                    memory_namespace=memory_namespace,
                    cwd=cwd,
                    source=source,
                    created_at=created_at,
                    updated_at=updated_at,
                )
        self._bot_map.clear()
        async with db.execute(
            "SELECT platform, bot_id, agent_name FROM bot_agent_map"
        ) as cur:
            async for row in cur:
                platform, bot_id, agent_name = row
                self._bot_map[(platform, bot_id)] = agent_name

    async def close(self) -> None:
        """Close the database connection and clear caches."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            self._agents.clear()
            self._bot_map.clear()
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

    def get_bot_agent(self, platform: str, bot_id: str) -> str | None:
        """Return agent_name for (platform, bot_id), or None."""
        self._require_db()
        return self._bot_map.get((platform, bot_id))

    def get_all_bot_mappings(self) -> dict[tuple[str, str], str]:
        """Return a snapshot of all (platform, bot_id) → agent_name mappings."""
        self._require_db()
        return dict(self._bot_map)

    # ------------------------------------------------------------------
    # Async writes
    # ------------------------------------------------------------------

    async def upsert(self, row: AgentRow) -> None:
        """Insert or update an agent row in DB and cache."""
        db = self._require_db()
        now = _utc_now_iso()
        await db.execute(
            "INSERT INTO agents "
            "(name, backend, model, max_turns, tools_json, persona, "
            "show_intermediate, smart_routing_json, plugins_json, "
            "memory_namespace, cwd, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "backend=excluded.backend, "
            "model=excluded.model, "
            "max_turns=excluded.max_turns, "
            "tools_json=excluded.tools_json, "
            "persona=excluded.persona, "
            "show_intermediate=excluded.show_intermediate, "
            "smart_routing_json=excluded.smart_routing_json, "
            "plugins_json=excluded.plugins_json, "
            "memory_namespace=excluded.memory_namespace, "
            "cwd=excluded.cwd, "
            "source=excluded.source, "
            "updated_at=?",
            (
                row.name,
                row.backend,
                row.model,
                row.max_turns,
                row.tools_json,
                row.persona,
                1 if row.show_intermediate else 0,
                row.smart_routing_json,
                row.plugins_json,
                row.memory_namespace,
                row.cwd,
                row.source,
                row.created_at,
                now,
                # ON CONFLICT updated_at value
                now,
            ),
        )
        await db.commit()
        # Update cache
        self._agents[row.name] = AgentRow(
            name=row.name,
            backend=row.backend,
            model=row.model,
            max_turns=row.max_turns,
            tools_json=row.tools_json,
            persona=row.persona,
            show_intermediate=row.show_intermediate,
            smart_routing_json=row.smart_routing_json,
            plugins_json=row.plugins_json,
            memory_namespace=row.memory_namespace,
            cwd=row.cwd,
            source=row.source,
            created_at=row.created_at,
            updated_at=now,
        )

    async def delete(self, name: str) -> None:
        """Delete an agent. Raises ValueError if any bot is still assigned to it."""
        db = self._require_db()
        async with db.execute(
            "SELECT COUNT(*) FROM bot_agent_map WHERE agent_name = ?", (name,)
        ) as cur:
            row = await cur.fetchone()
            count = row[0] if row else 0
        if count > 0:
            raise ValueError(
                f"Agent {name!r} is still assigned to one or more bots. "
                "Run 'lyra agent unassign' first."
            )
        await db.execute("DELETE FROM agents WHERE name = ?", (name,))
        await db.commit()
        self._agents.pop(name, None)

    async def set_bot_agent(self, platform: str, bot_id: str, agent_name: str) -> None:
        """Upsert a bot → agent mapping."""
        db = self._require_db()
        now = _utc_now_iso()
        await db.execute(
            "INSERT INTO bot_agent_map (platform, bot_id, agent_name, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(platform, bot_id) DO UPDATE SET "
            "agent_name=excluded.agent_name, "
            "updated_at=excluded.updated_at",
            (platform, bot_id, agent_name, now),
        )
        await db.commit()
        self._bot_map[(platform, bot_id)] = agent_name

    async def remove_bot_agent(self, platform: str, bot_id: str) -> None:
        """Remove a bot → agent mapping. No-op if it does not exist."""
        db = self._require_db()
        await db.execute(
            "DELETE FROM bot_agent_map WHERE platform = ? AND bot_id = ?",
            (platform, bot_id),
        )
        await db.commit()
        self._bot_map.pop((platform, bot_id), None)

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
            async for row in cur:
                agent_name, last_active_at, updated_at, pool_count, status = row
                result[agent_name] = AgentRuntimeStateRow(
                    agent_name=agent_name,
                    last_active_at=last_active_at,
                    updated_at=updated_at,
                    pool_count=pool_count,
                    status=status,
                )
        return result

    async def set_runtime_state(
        self, agent_name: str, status: str, pool_count: int = 0
    ) -> None:
        """Upsert runtime state for an agent."""
        _valid_statuses = {"idle", "active", "error"}
        if status not in _valid_statuses:
            raise ValueError(
                f"invalid status {status!r} — must be one of {sorted(_valid_statuses)}"
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
    # TOML seeding
    # ------------------------------------------------------------------

    async def seed_from_toml(self, path: Path, *, force: bool = False) -> int:
        """Import an agent from a TOML file. Returns 1 if imported, 0 otherwise.

        If force=False and the agent already exists in cache, skip the import.
        On parse error, log a warning and return 0.
        """
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception as exc:  # noqa: BLE001
            log.warning("seed_from_toml: failed to parse %s: %s", path, exc)
            return 0

        agent_section = data.get("agent", {})
        model_section = data.get("model", {})

        name = agent_section.get("name") or model_section.get("name")
        if not name:
            log.warning("seed_from_toml: no [agent].name in %s — skipped", path)
            return 0

        if not force and name in self._agents:
            return 0

        # Fields may live under [model] (wizard-generated) or [agent] (legacy).
        backend = model_section.get("backend") or agent_section.get(
            "backend", "anthropic-sdk"
        )
        model = model_section.get("model") or agent_section.get(
            "model", "claude-3-5-haiku-20241022"
        )
        max_turns = model_section.get("max_turns") or agent_section.get("max_turns", 10)
        tools_json = json.dumps(
            model_section.get("tools") or agent_section.get("tools", [])
        )
        persona = agent_section.get("persona")
        show_intermediate = agent_section.get("show_intermediate", False)
        smart_routing = agent_section.get("smart_routing")
        smart_routing_json = json.dumps(smart_routing) if smart_routing else None
        # plugins may live under [plugins].enabled (wizard) or [agent].plugins (legacy)
        plugins_json = json.dumps(
            data.get("plugins", {}).get("enabled") or agent_section.get("plugins", [])
        )
        memory_namespace = agent_section.get("memory_namespace")
        cwd = model_section.get("cwd") or agent_section.get("cwd")

        row = AgentRow(
            name=name,
            backend=backend,
            model=model,
            max_turns=max_turns,
            tools_json=tools_json,
            persona=persona,
            show_intermediate=show_intermediate,
            smart_routing_json=smart_routing_json,
            plugins_json=plugins_json,
            memory_namespace=memory_namespace,
            cwd=cwd,
            source="toml-seed",
        )
        await self.upsert(row)
        return 1
