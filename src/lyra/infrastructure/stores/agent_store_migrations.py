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
    await db.commit()
