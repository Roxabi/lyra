"""Database migration helpers for InstallationStore."""

from __future__ import annotations

import logging

import aiosqlite

log = logging.getLogger(__name__)

__all__ = ["CREATE_CONNECTOR_INSTALLATIONS", "run_ingress_migrations"]

CREATE_CONNECTOR_INSTALLATIONS = """
CREATE TABLE IF NOT EXISTS connector_installations (
    connector       TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    factory_tenant  TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    metadata_json   TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (connector, external_id)
)
"""


async def _get_user_version(db: aiosqlite.Connection) -> int:
    cur = await db.execute("PRAGMA user_version")
    row = await cur.fetchone()
    return row[0] if row else 0


async def _set_user_version(db: aiosqlite.Connection, version: int) -> None:
    await db.execute(f"PRAGMA user_version = {version}")


async def run_ingress_migrations(db: aiosqlite.Connection) -> None:
    """Run additive schema migrations for connector_installations.

    The CREATE TABLE statement is handled by ``_open_db(ddl=[...])`` in
    ``InstallationStore.connect()`` — matching the BotStore pattern.

    Future additive migrations (e.g. ``ALTER TABLE ADD COLUMN``) go here.

    Args:
        db: An open aiosqlite connection.
    """
    version = await _get_user_version(db)

    if version < 1:
        await _set_user_version(db, 1)

    await db.commit()