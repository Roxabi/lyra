"""SQL DDL and DML constants for the bots table."""

from __future__ import annotations

__all__ = [
    "_CREATE_BOTS",
    "_SELECT_BOTS",
    "_UPSERT_BOT",
    "_N_BOT_COLS",
]

_CREATE_BOTS = """
CREATE TABLE IF NOT EXISTS bots (
    platform TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    webhook_enabled INTEGER NOT NULL DEFAULT 0,
    auto_thread INTEGER NOT NULL DEFAULT 0,
    thread_hot_hours INTEGER NOT NULL DEFAULT 24,
    updated_at TEXT,
    public_bot TEXT,
    PRIMARY KEY (platform, bot_id)
)
"""

_SELECT_BOTS = (
    "SELECT platform, bot_id, agent, webhook_enabled, "
    "auto_thread, thread_hot_hours, updated_at, public_bot "
    "FROM bots"
)

_N_BOT_COLS = 8  # const-ok: bot table column count

_UPSERT_BOT = (
    f"INSERT INTO bots (platform, bot_id, agent, webhook_enabled, "
    f"auto_thread, thread_hot_hours, updated_at, public_bot) "
    f"VALUES ({', '.join(['?'] * _N_BOT_COLS)}) "
    "ON CONFLICT(platform, bot_id) DO UPDATE SET "
    "agent=excluded.agent, "
    "webhook_enabled=excluded.webhook_enabled, "
    "auto_thread=excluded.auto_thread, "
    "thread_hot_hours=excluded.thread_hot_hours, "
    "updated_at=excluded.updated_at, "
    "public_bot=excluded.public_bot"
)