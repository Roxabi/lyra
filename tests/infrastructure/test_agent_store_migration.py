"""Tests for AgentStore migrations.

Covers: effort column addition (#1101) and show_tool_recap drop (#1335).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import aiosqlite
import pytest

from lyra.core.agent.agent_schema import (
    _CREATE_AGENT_RUNTIME_STATE,
    _CREATE_AGENTS,
)
from lyra.infrastructure.stores.agent_store import AgentStore
from lyra.infrastructure.stores.agent_store_migrations import run_agent_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _col_names(pragma_rows: list[tuple]) -> list[str]:
    """Extract column names from PRAGMA table_info rows (cid, name, type, ...)."""
    return [row[1] for row in pragma_rows]


async def _pragma_table_info(db: aiosqlite.Connection, table: str) -> list[tuple]:
    async with db.execute(f"PRAGMA table_info('{table}')") as cur:
        rows = await cur.fetchall()
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# T1 — Fresh DB: CREATE TABLE includes effort from the start
# ---------------------------------------------------------------------------


class TestFreshDb:
    def test_create_ddl_contains_effort(self) -> None:
        """_CREATE_AGENTS DDL must declare effort TEXT column."""
        assert "effort TEXT" in _CREATE_AGENTS

    def test_create_ddl_does_not_contain_show_tool_recap(self) -> None:
        """_CREATE_AGENTS DDL must not declare the dropped show_tool_recap column."""
        assert "show_tool_recap" not in _CREATE_AGENTS

    @pytest.mark.asyncio
    async def test_fresh_db_has_effort_column(self) -> None:
        """A brand-new AgentStore has effort in its schema (24 columns)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "agents.db"
            store = AgentStore(db_path)
            await store.connect()
            try:
                db = store._require_db()
                rows = await _pragma_table_info(db, "agents")
                cols = _col_names(rows)
                assert "effort" in cols
                assert "show_tool_recap" not in cols
                assert len(rows) == 24
            finally:
                await store.close()


# ---------------------------------------------------------------------------
# T2 — Pre-existing legacy DB (show_tool_recap present, effort absent):
#      migration applies cleanly
# ---------------------------------------------------------------------------


_DDL_24_COL = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    max_turns INTEGER NOT NULL DEFAULT 10,
    tools_json TEXT NOT NULL DEFAULT '[]',
    show_intermediate INTEGER NOT NULL DEFAULT 0,
    smart_routing_json TEXT,
    plugins_json TEXT NOT NULL DEFAULT '[]',
    memory_namespace TEXT,
    cwd TEXT,
    source TEXT NOT NULL DEFAULT 'db',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    skip_permissions INTEGER NOT NULL DEFAULT 0,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    workspaces_json TEXT,
    commands_json TEXT,
    streaming INTEGER NOT NULL DEFAULT 0,
    persona_json TEXT,
    voice_json TEXT,
    fallback_language TEXT NOT NULL DEFAULT 'en',
    patterns_json TEXT,
    passthroughs_json TEXT,
    show_tool_recap INTEGER NOT NULL DEFAULT 1
)
"""

_DDL_BOT_AGENT_MAP = """
CREATE TABLE IF NOT EXISTS bot_agent_map (
    platform TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    settings_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, bot_id)
)
"""


async def _make_legacy_db(db_path: Path) -> None:
    """Create a legacy agents DB (show_tool_recap present, effort absent), 1 row."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_DDL_24_COL)
        await db.execute(_DDL_BOT_AGENT_MAP)
        await db.execute(_CREATE_AGENT_RUNTIME_STATE)
        await db.execute(
            "INSERT INTO agents (name, backend, model, show_tool_recap) "
            "VALUES (?, ?, ?, ?)",
            ("legacy-agent", "claude-cli", "claude-3-5-sonnet", 1),
        )
        await db.commit()


class TestLegacyDbMigration:
    @pytest.mark.asyncio
    async def test_migration_adds_effort_drops_show_tool_recap(self) -> None:
        """Migration on legacy DB adds effort, drops show_tool_recap (24 cols)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "agents.db"
            await _make_legacy_db(db_path)

            async with aiosqlite.connect(db_path) as db:
                await run_agent_migrations(db)
                rows = await _pragma_table_info(db, "agents")
                cols = _col_names(rows)
                assert "effort" in cols
                assert "show_tool_recap" not in cols
                assert len(rows) == 24

    @pytest.mark.asyncio
    async def test_existing_rows_have_null_effort(self) -> None:
        """Pre-existing rows must have effort IS NULL after migration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "agents.db"
            await _make_legacy_db(db_path)

            async with aiosqlite.connect(db_path) as db:
                await run_agent_migrations(db)
                async with db.execute(
                    "SELECT effort FROM agents WHERE name = ?", ("legacy-agent",)
                ) as cur:
                    row = await cur.fetchone()
                assert row is not None
                assert row[0] is None

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self) -> None:
        """Running migration twice on a 24-col DB is a no-op (no exception)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "agents.db"
            await _make_legacy_db(db_path)

            async with aiosqlite.connect(db_path) as db:
                await run_agent_migrations(db)
                # Second run must not raise
                await run_agent_migrations(db)
                rows = await _pragma_table_info(db, "agents")
                assert len(rows) == 24

    @pytest.mark.asyncio
    async def test_drop_show_tool_recap_idempotent(self) -> None:
        """PRAGMA guard prevents OperationalError when column already absent.

        Scenario: fresh DB (show_tool_recap never existed) → connect() twice.
        The second connect must not raise even though the DROP COLUMN
        migration runs again. Directly tests the
        ``if "show_tool_recap" in cols`` guard in run_agent_migrations.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "agents.db"
            # First connect: schema without show_tool_recap, migration is no-op
            store = AgentStore(db_path)
            await store.connect()
            await store.close()
            # Second connect: show_tool_recap still absent; guard prevents DROP
            store2 = AgentStore(db_path)
            await store2.connect()
            try:
                db = store2._require_db()
                rows = await _pragma_table_info(db, "agents")
                cols = _col_names(rows)
                assert "show_tool_recap" not in cols
                assert "effort" in cols
            finally:
                await store2.close()


# ---------------------------------------------------------------------------
# T3 — PRAGMA table_info reports 24 columns (fresh and post-migration)
# ---------------------------------------------------------------------------


class TestColumnCount:
    @pytest.mark.asyncio
    async def test_pragma_reports_24_cols_fresh(self) -> None:
        """Fresh DB: PRAGMA table_info('agents') returns 24 rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "agents.db"
            store = AgentStore(db_path)
            await store.connect()
            try:
                db = store._require_db()
                rows = await _pragma_table_info(db, "agents")
                assert len(rows) == 24
            finally:
                await store.close()

    @pytest.mark.asyncio
    async def test_pragma_reports_24_cols_after_migration(self) -> None:
        """Legacy DB: PRAGMA table_info('agents') returns 24 rows after migration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "agents.db"
            await _make_legacy_db(db_path)

            async with aiosqlite.connect(db_path) as db:
                await run_agent_migrations(db)
                rows = await _pragma_table_info(db, "agents")
                assert len(rows) == 24
