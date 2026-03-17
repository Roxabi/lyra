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
    persona TEXT,
    show_intermediate INTEGER NOT NULL DEFAULT 0,
    smart_routing_json TEXT,
    plugins_json TEXT NOT NULL DEFAULT '[]',
    memory_namespace TEXT,
    cwd TEXT,
    source TEXT NOT NULL DEFAULT 'db',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    tts_json TEXT,
    stt_json TEXT,
    skip_permissions INTEGER NOT NULL DEFAULT 0,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    workspaces_json TEXT,
    i18n_language TEXT NOT NULL DEFAULT 'en',
    commands_json TEXT
)
"""

# Additive migrations — ALTER TABLE ADD COLUMN is idempotent (OperationalError caught).
_MIGRATE_AGENTS = [
    "ALTER TABLE agents ADD COLUMN tts_json TEXT",
    "ALTER TABLE agents ADD COLUMN stt_json TEXT",
    "ALTER TABLE agents ADD COLUMN skip_permissions INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE agents ADD COLUMN workspaces_json TEXT",
    "ALTER TABLE agents ADD COLUMN i18n_language TEXT NOT NULL DEFAULT 'en'",
    "ALTER TABLE agents ADD COLUMN commands_json TEXT",
    "ALTER TABLE agents ADD COLUMN streaming INTEGER NOT NULL DEFAULT 0",
    # #343 — DB-first agent config: inline persona, merge voice, fallback_language
    "ALTER TABLE agents ADD COLUMN persona_json TEXT",
    "ALTER TABLE agents ADD COLUMN voice_json TEXT",
    "ALTER TABLE agents ADD COLUMN fallback_language TEXT NOT NULL DEFAULT 'en'",
    "ALTER TABLE agents ADD COLUMN patterns_json TEXT",
    # #347 — per-bot settings (watch_channels etc.)
    "ALTER TABLE bot_agent_map ADD COLUMN settings_json TEXT",
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
_AGENT_COLUMNS = (
    "name, backend, model, max_turns, tools_json, persona, "
    "show_intermediate, smart_routing_json, plugins_json, "
    "memory_namespace, cwd, source, created_at, updated_at, "
    "tts_json, stt_json, skip_permissions, "
    "permissions_json, workspaces_json, i18n_language, commands_json, streaming, "
    "persona_json, voice_json, fallback_language, patterns_json"
)

_SELECT_AGENTS = f"SELECT {_AGENT_COLUMNS} FROM agents"

_UPSERT_AGENT = (
    f"INSERT INTO agents ({_AGENT_COLUMNS}) "
    f"VALUES ({', '.join(['?'] * 26)}) "
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
    "tts_json=excluded.tts_json, "
    "stt_json=excluded.stt_json, "
    "skip_permissions=excluded.skip_permissions, "
    "permissions_json=excluded.permissions_json, "
    "workspaces_json=excluded.workspaces_json, "
    "i18n_language=excluded.i18n_language, "
    "commands_json=excluded.commands_json, "
    "streaming=excluded.streaming, "
    "persona_json=COALESCE(excluded.persona_json, agents.persona_json), "
    "voice_json=COALESCE(excluded.voice_json, agents.voice_json), "
    "fallback_language=excluded.fallback_language, "
    "patterns_json=COALESCE(excluded.patterns_json, agents.patterns_json), "
    "source=excluded.source, "
    "updated_at=?"
)
