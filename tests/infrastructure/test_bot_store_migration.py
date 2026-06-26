"""Tests for BotStore migrations.

Covers: PRAGMA user_version guard for ALTER TABLE migration (#1454).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import aiosqlite
import pytest

from factory.core.agent.schema.bot_schema import _CREATE_BOTS
from factory.infrastructure.stores.migrations.bot_store_migrations import (
    _get_user_version,
    _set_user_version,
    run_bot_migrations,
)

_TARGET_COLS = {
    "platform",
    "bot_id",
    "agent",
    "webhook_enabled",
    "auto_thread",
    "thread_hot_hours",
    "updated_at",
    "public_bot",
}

# Legacy DDL without trusted_roles_json (pre-#1416 schema)
_DDL_LEGACY_BOTS = """
CREATE TABLE IF NOT EXISTS bots (
    platform TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    webhook_enabled INTEGER NOT NULL DEFAULT 0,
    default_trust TEXT NOT NULL DEFAULT 'blocked',
    owner_users_json TEXT NOT NULL DEFAULT '[]',
    trusted_users_json TEXT NOT NULL DEFAULT '[]',
    auto_thread INTEGER NOT NULL DEFAULT 0,
    thread_hot_hours INTEGER NOT NULL DEFAULT 24,
    updated_at TEXT,
    PRIMARY KEY (platform, bot_id)
)
"""

# Migration-1 schema: has trusted_roles_json but NOT public_bot (pre-#1984).
_DDL_V1_BOTS = """
CREATE TABLE IF NOT EXISTS bots (
    platform TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    webhook_enabled INTEGER NOT NULL DEFAULT 0,
    default_trust TEXT NOT NULL DEFAULT 'blocked',
    owner_users_json TEXT NOT NULL DEFAULT '[]',
    trusted_users_json TEXT NOT NULL DEFAULT '[]',
    trusted_roles_json TEXT NOT NULL DEFAULT '[]',
    auto_thread INTEGER NOT NULL DEFAULT 0,
    thread_hot_hours INTEGER NOT NULL DEFAULT 24,
    updated_at TEXT,
    PRIMARY KEY (platform, bot_id)
)
"""


async def _bot_columns(db: aiosqlite.Connection) -> set[str]:
    async with db.execute("PRAGMA table_info('bots')") as cur:
        rows = await cur.fetchall()
    return {row[1] for row in rows}


class TestFreshDbMigration:
    @pytest.mark.asyncio
    async def test_migration_normalizes_schema_and_sets_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_CREATE_BOTS)
                await db.commit()

                assert await _get_user_version(db) == 0

                await run_bot_migrations(db)

                assert await _get_user_version(db) == 3
                assert await _bot_columns(db) == _TARGET_COLS


class TestMigrationIdempotency:
    @pytest.mark.asyncio
    async def test_second_run_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_CREATE_BOTS)
                await db.commit()

                await run_bot_migrations(db)
                version_first = await _get_user_version(db)
                assert version_first == 3

                await run_bot_migrations(db)
                version_second = await _get_user_version(db)
                assert version_second == 3

    @pytest.mark.asyncio
    async def test_migration_skips_when_version_already_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_DDL_LEGACY_BOTS)
                await db.commit()
                await _set_user_version(db, 3)

                await run_bot_migrations(db)

                cols = await _bot_columns(db)
                assert "default_trust" in cols
                assert "public_bot" not in cols


class TestLegacyDbMigration:
    @pytest.mark.asyncio
    async def test_legacy_db_drops_auth_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_DDL_LEGACY_BOTS)
                await db.commit()

                assert await _get_user_version(db) == 0

                await run_bot_migrations(db)

                assert await _get_user_version(db) == 3
                cols = await _bot_columns(db)
                assert cols == _TARGET_COLS
                assert "default_trust" not in cols


class TestPublicBotMigration:
    @pytest.mark.asyncio
    async def test_v1_db_migrates_to_v3_with_public_bot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_DDL_V1_BOTS)
                await _set_user_version(db, 1)
                await db.commit()

                await run_bot_migrations(db)

                assert await _get_user_version(db) == 3
                assert await _bot_columns(db) == _TARGET_COLS

    @pytest.mark.asyncio
    async def test_legacy_rows_preserve_adapter_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_DDL_V1_BOTS)
                await _set_user_version(db, 1)
                await db.execute(
                    "INSERT INTO bots (platform, bot_id, agent) VALUES (?, ?, ?)",
                    ("telegram", "legacy", "lyra_default"),
                )
                await db.commit()

                await run_bot_migrations(db)

                async with db.execute(
                    "SELECT agent, public_bot FROM bots WHERE bot_id = 'legacy'"
                ) as cur:
                    row = await cur.fetchone()
                assert row is not None
                assert row[0] == "lyra_default"
                assert row[1] is None