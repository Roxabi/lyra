"""SQL DDL constants for the agent store tables."""

from __future__ import annotations

__all__ = [
    "_CREATE_AGENTS",
    "_MIGRATE_AGENTS",
    "_CREATE_BOT_AGENT_MAP",
    "_CREATE_AGENT_RUNTIME_STATE",
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
    skip_permissions INTEGER NOT NULL DEFAULT 0
)
"""

# Additive migrations — ALTER TABLE ADD COLUMN is idempotent (OperationalError caught).
_MIGRATE_AGENTS = [
    "ALTER TABLE agents ADD COLUMN tts_json TEXT",
    "ALTER TABLE agents ADD COLUMN stt_json TEXT",
    "ALTER TABLE agents ADD COLUMN skip_permissions INTEGER NOT NULL DEFAULT 0",
]

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
