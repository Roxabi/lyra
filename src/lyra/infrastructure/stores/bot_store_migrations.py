"""Database migration helpers for BotStore."""

from __future__ import annotations

import logging

import aiosqlite

log = logging.getLogger(__name__)

__all__ = ["run_bot_migrations"]


async def run_bot_migrations(db: aiosqlite.Connection) -> None:
    """Run additive schema migrations for the bots table.

    The CREATE TABLE statement is handled by ``_open_db(ddl=[_CREATE_BOTS])``
    in ``BotStore.connect()`` — matching the AgentStore pattern.

    Future additive migrations (e.g. ``ALTER TABLE ADD COLUMN``) go here.

    Args:
        db: An open aiosqlite connection.
    """
    # Migration: add trusted_roles_json column for #1416
    cur = await db.execute(
        "SELECT 1 FROM pragma_table_info('bots') WHERE name = 'trusted_roles_json'"
    )
    if await cur.fetchone() is None:
        await db.execute(
            "ALTER TABLE bots ADD COLUMN trusted_roles_json TEXT NOT NULL DEFAULT '[]'"
        )
