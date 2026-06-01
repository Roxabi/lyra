"""Tests for BotStore migrations.

Covers: PRAGMA user_version guard for ALTER TABLE migration (#1454).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import aiosqlite
import pytest

from lyra.core.agent.schema.bot_schema import _CREATE_BOTS
from lyra.infrastructure.stores.migrations.bot_store_migrations import (
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
        """Fresh DB: migration adds trusted_roles_json and sets user_version=1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_CREATE_BOTS)
                await db.commit()

                version_before = await _get_user_version(db)
                assert version_before == 0

                await run_bot_migrations(db)

                version_after = await _get_user_version(db)
                assert version_after == 1

                async with db.execute("PRAGMA table_info('bots')") as cur:
                    rows = await cur.fetchall()
                cols = {row[1] for row in rows}
                assert "trusted_roles_json" in cols


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
                assert version_first == 1

                # Second run must not raise
                await run_bot_migrations(db)
                version_second = await _get_user_version(db)
                assert version_second == 1

    @pytest.mark.asyncio
    async def test_migration_skips_when_version_already_set(self) -> None:
        """Migration skips ALTER TABLE when user_version >= 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bots.db"
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute(_DDL_LEGACY_BOTS)
                await db.commit()

                # Manually set version to 1 before running migration
                await _set_user_version(db, 1)

                # Migration must skip because version >= 1
                await run_bot_migrations(db)

                # Column should NOT be added because version guard skipped
                async with db.execute("PRAGMA table_info('bots')") as cur:
                    rows = await cur.fetchall()
                cols = {row[1] for row in rows}
                assert "trusted_roles_json" not in cols


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
                assert version_after == 1

                async with db.execute("PRAGMA table_info('bots')") as cur:
                    rows = await cur.fetchall()
                cols = {row[1] for row in rows}
                assert "trusted_roles_json" in cols
