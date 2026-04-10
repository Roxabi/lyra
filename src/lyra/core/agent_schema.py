"""SQL DDL and DML constants for the agent store tables."""

from __future__ import annotations

__all__ = [
    "_CREATE_AGENTS",
    "_MIGRATE_AGENTS",
    "_CREATE_BOT_AGENT_MAP",
    "_CREATE_AGENT_RUNTIME_STATE",
    "_SELECT_AGENTS",
    "_UPSERT_AGENT",
]

_CREATE_AGENTS = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    max_turns INTEGER NOT NULL DEFAULT 10,
    tools_json TEXT NOT NULL DEFAULT '[]',
    show_intermediate INTEGER NOT NULL DEFAULT 0,
    smart_routing_json TEXT,
    plugins_json TEXT NOT NULL DEFAULT '[]',
    memory_namespace TEXT,
    cwd TEXT,
    source TEXT NOT NULL DEFAULT 'db',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    skip_permissions INTEGER NOT NULL DEFAULT 0,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    workspaces_json TEXT,
    commands_json TEXT,
    streaming INTEGER NOT NULL DEFAULT 0,
    persona_json TEXT,
    voice_json TEXT,
    fallback_language TEXT NOT NULL DEFAULT 'en',
    patterns_json TEXT,
    passthroughs_json TEXT,
    show_tool_recap INTEGER NOT NULL DEFAULT 1
)
"""

# Additive migrations — ALTER TABLE ADD COLUMN is idempotent (OperationalError caught).
_MIGRATE_AGENTS = [
    "ALTER TABLE agents ADD COLUMN skip_permissions INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE agents ADD COLUMN workspaces_json TEXT",
    "ALTER TABLE agents ADD COLUMN commands_json TEXT",
    "ALTER TABLE agents ADD COLUMN streaming INTEGER NOT NULL DEFAULT 0",
    # #343 — DB-first agent config: inline persona, merge voice, fallback_language
    "ALTER TABLE agents ADD COLUMN persona_json TEXT",
    "ALTER TABLE agents ADD COLUMN voice_json TEXT",
    "ALTER TABLE agents ADD COLUMN fallback_language TEXT NOT NULL DEFAULT 'en'",
    "ALTER TABLE agents ADD COLUMN patterns_json TEXT",
    # #347 — per-bot settings (watch_channels etc.)
    "ALTER TABLE bot_agent_map ADD COLUMN settings_json TEXT",
    # passthroughs: agent-level list of commands forwarded straight to the LLM
    "ALTER TABLE agents ADD COLUMN passthroughs_json TEXT",
    "ALTER TABLE agents ADD COLUMN show_tool_recap INTEGER NOT NULL DEFAULT 1",
]

_CREATE_BOT_AGENT_MAP = """
CREATE TABLE IF NOT EXISTS bot_agent_map (
    platform TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    settings_json TEXT,
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

# Column list shared by SELECT and INSERT to keep them in sync.
# 24 columns after #346 cleanup (dropped: tts_json, stt_json, i18n_language).
_AGENT_COLUMNS = (
    "name, backend, model, max_turns, tools_json, "
    "show_intermediate, smart_routing_json, plugins_json, "
    "memory_namespace, cwd, source, created_at, updated_at, "
    "skip_permissions, permissions_json, workspaces_json, commands_json, streaming, "
    "persona_json, voice_json, fallback_language, patterns_json, passthroughs_json, "
    "show_tool_recap"
)

_SELECT_AGENTS = f"SELECT {_AGENT_COLUMNS} FROM agents"

_N_AGENT_COLS = len([c.strip() for c in _AGENT_COLUMNS.split(",") if c.strip()])

_UPSERT_AGENT = (
    f"INSERT INTO agents ({_AGENT_COLUMNS}) "
    f"VALUES ({', '.join(['?'] * _N_AGENT_COLS)}) "
    "ON CONFLICT(name) DO UPDATE SET "
    "backend=excluded.backend, "
    "model=excluded.model, "
    "max_turns=excluded.max_turns, "
    "tools_json=excluded.tools_json, "
    "show_intermediate=excluded.show_intermediate, "
    "smart_routing_json=excluded.smart_routing_json, "
    "plugins_json=excluded.plugins_json, "
    "memory_namespace=excluded.memory_namespace, "
    "cwd=excluded.cwd, "
    "skip_permissions=excluded.skip_permissions, "
    "permissions_json=excluded.permissions_json, "
    "workspaces_json=excluded.workspaces_json, "
    "commands_json=excluded.commands_json, "
    "streaming=excluded.streaming, "
    "persona_json=COALESCE(excluded.persona_json, agents.persona_json), "
    "voice_json=COALESCE(excluded.voice_json, agents.voice_json), "
    "fallback_language=excluded.fallback_language, "
    "patterns_json=COALESCE(excluded.patterns_json, agents.patterns_json), "
    "passthroughs_json=COALESCE(excluded.passthroughs_json, agents.passthroughs_json), "
    "show_tool_recap=excluded.show_tool_recap, "
    "source=excluded.source, "
    "updated_at=?"
)
