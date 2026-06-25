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


# ---------------------------------------------------------------------------
# T1 — Fresh DB: migration runs and sets user_version
# ---------------------------------------------------------------------------


class TestFreshDbMigration:
    @pytest.mark.asyncio
    async def test_migration_adds_column_and_sets_version(self) -> None:
        """Fresh DB: migrations add columns and set user_version to latest (2)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_CREATE_BOTS)
                await db.commit()

                version_before = await _get_user_version(db)
                assert version_before == 0

                await run_bot_migrations(db)

                version_after = await _get_user_version(db)
                assert version_after == 2

                async with db.execute("PRAGMA table_info('bots')") as cur:
                    rows = await cur.fetchall()
                cols = {row[1] for row in rows}
                assert "trusted_roles_json" in cols
                assert "public_bot" in cols


# ---------------------------------------------------------------------------
# T2 — Idempotency: second run is a no-op
# ---------------------------------------------------------------------------


class TestMigrationIdempotency:
    @pytest.mark.asyncio
    async def test_second_run_is_noop(self) -> None:
        """Running migration twice on a fresh DB is a no-op (no exception)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_CREATE_BOTS)
                await db.commit()

                await run_bot_migrations(db)
                version_first = await _get_user_version(db)
                assert version_first == 2

                # Second run must not raise
                await run_bot_migrations(db)
                version_second = await _get_user_version(db)
                assert version_second == 2

    @pytest.mark.asyncio
    async def test_migration_skips_when_version_already_set(self) -> None:
        """Migration skips every ALTER TABLE when user_version is at latest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_DDL_LEGACY_BOTS)
                await db.commit()

                # Manually set version to latest before running migration
                await _set_user_version(db, 2)

                # Migration must skip because version is already at latest
                await run_bot_migrations(db)

                # No column added because every version guard skipped
                async with db.execute("PRAGMA table_info('bots')") as cur:
                    rows = await cur.fetchall()
                cols = {row[1] for row in rows}
                assert "trusted_roles_json" not in cols
                assert "public_bot" not in cols


# ---------------------------------------------------------------------------
# T3 — Legacy DB without user_version: migration applies
# ---------------------------------------------------------------------------


class TestLegacyDbMigration:
    @pytest.mark.asyncio
    async def test_legacy_db_gets_migration_and_version(self) -> None:
        """Pre-existing DB without user_version gets migrated and version set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                # Create legacy schema without trusted_roles_json
                await db.execute(_DDL_LEGACY_BOTS)
                await db.commit()

                # Verify user_version starts at 0
                version = await _get_user_version(db)
                assert version == 0

                await run_bot_migrations(db)

                version_after = await _get_user_version(db)
                assert version_after == 2

                async with db.execute("PRAGMA table_info('bots')") as cur:
                    rows = await cur.fetchall()
                cols = {row[1] for row in rows}
                assert "trusted_roles_json" in cols
                assert "public_bot" in cols


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


# ---------------------------------------------------------------------------
# T4 — Migration 2 (#1984): add public_bot to a v1 DB, leave legacy rows NULL
# ---------------------------------------------------------------------------


class TestPublicBotMigration:
    @pytest.mark.asyncio
    async def test_v1_db_gains_public_bot_column(self) -> None:
        """A version-1 DB migrates up: public_bot added, version becomes 2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_DDL_V1_BOTS)
                await _set_user_version(db, 1)
                await db.commit()

                await run_bot_migrations(db)

                assert await _get_user_version(db) == 2
                async with db.execute("PRAGMA table_info('bots')") as cur:
                    cols = {row[1] for row in await cur.fetchall()}
                assert "public_bot" in cols

    @pytest.mark.asyncio
    async def test_legacy_rows_default_public_bot_null(self) -> None:
        """Rows written before the migration read back with public_bot = NULL."""
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
                    "SELECT public_bot FROM bots WHERE bot_id = 'legacy'"
                ) as cur:
                    row = await cur.fetchone()
                assert row is not None
                assert row[0] is None
