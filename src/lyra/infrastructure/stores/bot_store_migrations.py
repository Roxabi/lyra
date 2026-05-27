"""Database migration helpers for BotStore."""

from __future__ import annotations

import logging

import aiosqlite

log = logging.getLogger(__name__)

__all__ = ["run_bot_migrations"]

_CREATE_BOTS = """
CREATE TABLE bots (
    platform TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    webhook_enabled INTEGER NOT NULL DEFAULT 0,
    default_trust TEXT NOT NULL DEFAULT 'untrusted',
    owner_users_json TEXT NOT NULL DEFAULT '[]',
    trusted_users_json TEXT NOT NULL DEFAULT '[]',
    auto_thread INTEGER NOT NULL DEFAULT 0,
    thread_hot_hours INTEGER NOT NULL DEFAULT 24,
    updated_at TEXT,
    PRIMARY KEY (platform, bot_id)
)
"""


async def run_bot_migrations(db: aiosqlite.Connection) -> None:
    """Run schema migrations for the bots table.

    The CREATE TABLE statement is run once; future
    additive migrations will append ``ALTER TABLE`` statements here.

    Args:
        db: An open aiosqlite connection.
    """
    await db.execute(_CREATE_BOTS)
    await db.commit()
