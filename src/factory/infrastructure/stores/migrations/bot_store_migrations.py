"""Database migration helpers for BotStore."""

from __future__ import annotations

import logging

import aiosqlite

log = logging.getLogger(__name__)

__all__ = ["run_bot_migrations"]


async def _get_user_version(db: aiosqlite.Connection) -> int:
    cur = await db.execute("PRAGMA user_version")
    row = await cur.fetchone()
    return row[0] if row else 0


async def _set_user_version(db: aiosqlite.Connection, version: int) -> None:
    await db.execute(f"PRAGMA user_version = {version}")


async def run_bot_migrations(db: aiosqlite.Connection) -> None:
    """Run additive schema migrations for the bots table.

    The CREATE TABLE statement is handled by ``_open_db(ddl=[_CREATE_BOTS])``
    in ``BotStore.connect()`` — matching the AgentStore pattern.

    Future additive migrations (e.g. ``ALTER TABLE ADD COLUMN``) go here.

    Args:
        db: An open aiosqlite connection.
    """
    version = await _get_user_version(db)

    if version < 1:
        # Migration 1: add trusted_roles_json column for #1416
        cur = await db.execute(
            "SELECT 1 FROM pragma_table_info('bots') WHERE name = 'trusted_roles_json'"
        )
        if await cur.fetchone() is None:
            await db.execute(
                "ALTER TABLE bots ADD COLUMN trusted_roles_json"
                " TEXT NOT NULL DEFAULT '[]'"
            )
        await _set_user_version(db, 1)

    if version < 2:
        # Migration 2: add public_bot column for #1984 (ADR-090 §5 refusal
        # pointer). Nullable, no default — legacy rows stay NULL and the deny
        # refusal degrades to the generic factory.roxabi.dev pointer.
        cur = await db.execute(
            "SELECT 1 FROM pragma_table_info('bots') WHERE name = 'public_bot'"
        )
        if await cur.fetchone() is None:
            await db.execute("ALTER TABLE bots ADD COLUMN public_bot TEXT DEFAULT NULL")
        await _set_user_version(db, 2)

    if version < 3:
        # Migration 3: drop legacy auth columns (identity/grants are store SSOT).
        _TARGET_COLS = frozenset(
            {
                "platform",
                "bot_id",
                "agent",
                "webhook_enabled",
                "auto_thread",
                "thread_hot_hours",
                "updated_at",
                "public_bot",
            }
        )
        _LEGACY_COLS = frozenset(
            {
                "default_trust",
                "owner_users_json",
                "trusted_users_json",
                "trusted_roles_json",
            }
        )
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bots'"
        )
        if await cur.fetchone() is not None:
            async with db.execute("PRAGMA table_info('bots')") as info_cur:
                cols = {row[1] for row in await info_cur.fetchall()}
            if cols != _TARGET_COLS or (cols & _LEGACY_COLS):
                await db.execute("DROP TABLE IF EXISTS bots__v3")
                await db.execute(
                    """
                    CREATE TABLE bots__v3 (
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
                )
                await db.execute(
                    """
                    INSERT INTO bots__v3 (
                        platform, bot_id, agent, webhook_enabled,
                        auto_thread, thread_hot_hours, updated_at, public_bot
                    )
                    SELECT
                        platform, bot_id, agent, webhook_enabled,
                        auto_thread, thread_hot_hours, updated_at, public_bot
                    FROM bots
                    """
                )
                await db.execute("DROP TABLE bots")
                await db.execute("ALTER TABLE bots__v3 RENAME TO bots")
        await _set_user_version(db, 3)

    await db.commit()
