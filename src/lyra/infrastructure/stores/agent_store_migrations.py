"""Database migration helpers for AgentStore."""

from __future__ import annotations

import logging

import aiosqlite

from lyra.core.agent.agent_schema import _MIGRATE_AGENTS

log = logging.getLogger(__name__)

__all__ = ["run_agent_migrations"]


async def run_agent_migrations(db: aiosqlite.Connection) -> None:
    """Run additive schema migrations for the agents and bot_agent_map tables.

    Each statement in ``_MIGRATE_AGENTS`` is an ``ALTER TABLE ADD COLUMN``.
    These are idempotent: if the column already exists, the statement raises
    ``OperationalError("duplicate column name")`` which we silently ignore.

    Args:
        db: An open aiosqlite connection.
    """
    for stmt in _MIGRATE_AGENTS:
        try:
            await db.execute(stmt)
        except aiosqlite.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    # Drop show_tool_recap column if present (issue #1335).
    # SQLite 3.45.1 supports DROP COLUMN natively; PRAGMA guard makes the migration
    # idempotent so a second connect on an already-migrated DB does not raise.
    cur = await db.execute("PRAGMA table_info(agents)")
    cols = {row[1] for row in await cur.fetchall()}
    if "show_tool_recap" in cols:
        await db.execute("ALTER TABLE agents DROP COLUMN show_tool_recap")

    await db.commit()
