"""Tests for multibot_stores migration functions (#417)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lyra.bootstrap.bootstrap_stores import (
    _atomic_table_copy,
    _ensure_config_db,
    _ensure_discord_db,
    _has_sentinel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_auth_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE agents (name TEXT PRIMARY KEY, display_name TEXT)")
    conn.execute("INSERT INTO agents VALUES ('test-agent', 'Test Agent')")
    conn.execute(
        "CREATE TABLE bot_agent_map (platform TEXT, bot_id TEXT, agent_name TEXT)"
    )
    conn.execute("CREATE TABLE agent_runtime_state (agent_name TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE bot_secrets (bot_id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE user_prefs"
        " (user_id TEXT, key TEXT, value TEXT, PRIMARY KEY (user_id, key))"
    )
    conn.execute(
        "CREATE TABLE discord_threads"
        " (thread_id TEXT, bot_id TEXT, PRIMARY KEY (thread_id, bot_id))"
    )
    conn.commit()
    conn.close()


def _create_db_with_sentinel(path: Path) -> None:
    """Create a minimal DB that already has the migration sentinel."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migration_complete (migrated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO _migration_complete (migrated_at) VALUES (datetime('now'))"
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# _has_sentinel
# ---------------------------------------------------------------------------


class TestHasSentinel:
    def test_has_sentinel_missing_file(self, tmp_path: Path) -> None:
        # Arrange
        path = tmp_path / "nonexistent.db"
        # Act
        result = _has_sentinel(path)
        # Assert
        assert result is False

    def test_has_sentinel_no_table(self, tmp_path: Path) -> None:
        # Arrange — DB exists but has no _migration_complete table
        path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE other_table (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        # Act
        result = _has_sentinel(path)
        # Assert
        assert result is False

    def test_has_sentinel_with_row(self, tmp_path: Path) -> None:
        # Arrange — DB with sentinel table AND a row
        path = tmp_path / "complete.db"
        _create_db_with_sentinel(path)
        # Act
        result = _has_sentinel(path)
        # Assert
        assert result is True

    def test_has_sentinel_table_exists_but_empty(self, tmp_path: Path) -> None:
        # Arrange — table exists but has no rows (partial crash scenario)
        path = tmp_path / "partial.db"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE _migration_complete (migrated_at TEXT NOT NULL)")
        conn.commit()
        conn.close()
        # Act
        result = _has_sentinel(path)
        # Assert
        assert result is False


# ---------------------------------------------------------------------------
# _atomic_table_copy
# ---------------------------------------------------------------------------


class TestAtomicTableCopy:
    def test_atomic_table_copy_basic(self, tmp_path: Path) -> None:
        # Arrange — auth.db with agents table containing one row
        src = tmp_path / "auth.db"
        dst = tmp_path / "config.db"
        _create_auth_db(src)
        # Act
        rows_copied = _atomic_table_copy(
            src_path=src,
            dst_path=dst,
            tables=("agents",),
            tmp_prefix=".test_copy_",
        )
        # Assert — rows were copied and sentinel was written
        assert rows_copied == 1
        assert dst.exists()
        conn = sqlite3.connect(str(dst))
        agents = conn.execute("SELECT name FROM agents").fetchall()
        assert len(agents) == 1
        assert agents[0][0] == "test-agent"
        sentinel = conn.execute("SELECT 1 FROM _migration_complete LIMIT 1").fetchone()
        assert sentinel is not None
        conn.close()

    def test_atomic_table_copy_skips_missing_table(self, tmp_path: Path) -> None:
        # Arrange — auth.db does NOT have the requested table
        src = tmp_path / "auth.db"
        dst = tmp_path / "config.db"
        conn = sqlite3.connect(str(src))
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        # Act — request a table that doesn't exist in source
        rows_copied = _atomic_table_copy(
            src_path=src,
            dst_path=dst,
            tables=("nonexistent_table",),
            tmp_prefix=".test_missing_",
        )
        # Assert — 0 rows, dst still created (with empty + sentinel)
        assert rows_copied == 0
        assert dst.exists()
        assert _has_sentinel(dst)

    def test_atomic_table_copy_missing_source(self, tmp_path: Path) -> None:
        # Arrange — source DB does not exist
        src = tmp_path / "missing_auth.db"
        dst = tmp_path / "config.db"
        # Act
        rows_copied = _atomic_table_copy(
            src_path=src,
            dst_path=dst,
            tables=("agents",),
            tmp_prefix=".test_no_src_",
        )
        # Assert — returns 0, destination not created
        assert rows_copied == 0
        assert not dst.exists()

    def test_atomic_table_copy_copies_all_requested_tables(
        self, tmp_path: Path
    ) -> None:
        # Arrange — auth.db with multiple tables, each with rows
        src = tmp_path / "auth.db"
        dst = tmp_path / "config.db"
        _create_auth_db(src)
        conn = sqlite3.connect(str(src))
        conn.execute(
            "INSERT INTO bot_agent_map VALUES ('telegram', 'main', 'test-agent')"
        )
        conn.commit()
        conn.close()
        # Act
        rows_copied = _atomic_table_copy(
            src_path=src,
            dst_path=dst,
            tables=("agents", "bot_agent_map"),
            tmp_prefix=".test_multi_",
        )
        # Assert — 1 agent row + 1 map row
        assert rows_copied == 2
        conn = sqlite3.connect(str(dst))
        assert conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM bot_agent_map").fetchone()[0] == 1
        conn.close()


# ---------------------------------------------------------------------------
# _ensure_config_db
# ---------------------------------------------------------------------------


class TestEnsureConfigDb:
    def test_ensure_config_db_fresh(self, tmp_path: Path) -> None:
        # Arrange — auth.db exists, config.db does NOT exist
        _create_auth_db(tmp_path / "auth.db")
        config_path = tmp_path / "config.db"
        assert not config_path.exists()
        # Act
        _ensure_config_db(tmp_path)
        # Assert — config.db was created with sentinel
        assert config_path.exists()
        assert _has_sentinel(config_path)

    def test_ensure_config_db_partial(self, tmp_path: Path) -> None:
        # Arrange — config.db exists WITHOUT sentinel (partial crash)
        _create_auth_db(tmp_path / "auth.db")
        config_path = tmp_path / "config.db"
        conn = sqlite3.connect(str(config_path))
        conn.execute("CREATE TABLE agents (name TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        mtime_before = config_path.stat().st_mtime
        # Act
        _ensure_config_db(tmp_path)
        # Assert — partial DB was deleted and re-created with sentinel
        assert config_path.exists()
        assert _has_sentinel(config_path)
        # The file should have been recreated (different inode/mtime)
        mtime_after = config_path.stat().st_mtime
        assert mtime_after >= mtime_before  # at minimum it was touched

    def test_ensure_config_db_complete_noop(self, tmp_path: Path) -> None:
        # Arrange — config.db already exists WITH sentinel
        _create_auth_db(tmp_path / "auth.db")
        config_path = tmp_path / "config.db"
        _create_db_with_sentinel(config_path)
        mtime_before = config_path.stat().st_mtime
        # Act
        _ensure_config_db(tmp_path)
        # Assert — file was NOT touched (same mtime)
        assert config_path.exists()
        assert _has_sentinel(config_path)
        assert config_path.stat().st_mtime == mtime_before

    def test_ensure_config_db_no_auth_db(self, tmp_path: Path) -> None:
        # Arrange — neither auth.db nor config.db exist
        config_path = tmp_path / "config.db"
        # Act — should not raise
        _ensure_config_db(tmp_path)
        # Assert — config.db still absent (nothing to migrate from)
        assert not config_path.exists()


# ---------------------------------------------------------------------------
# _ensure_discord_db
# ---------------------------------------------------------------------------


class TestEnsureDiscordDb:
    def test_ensure_discord_db_fresh(self, tmp_path: Path) -> None:
        # Arrange — auth.db with discord_threads table, discord.db absent
        _create_auth_db(tmp_path / "auth.db")
        conn = sqlite3.connect(str(tmp_path / "auth.db"))
        conn.execute("INSERT INTO discord_threads VALUES ('thread-1', 'main')")
        conn.commit()
        conn.close()
        discord_path = tmp_path / "discord.db"
        assert not discord_path.exists()
        # Act
        _ensure_discord_db(tmp_path)
        # Assert — discord.db was created with sentinel
        assert discord_path.exists()
        assert _has_sentinel(discord_path)

    def test_ensure_discord_db_partial(self, tmp_path: Path) -> None:
        # Arrange — discord.db exists WITHOUT sentinel
        _create_auth_db(tmp_path / "auth.db")
        discord_path = tmp_path / "discord.db"
        conn = sqlite3.connect(str(discord_path))
        conn.execute(
            "CREATE TABLE discord_threads"
            " (thread_id TEXT, bot_id TEXT, PRIMARY KEY (thread_id, bot_id))"
        )
        conn.commit()
        conn.close()
        # Act
        _ensure_discord_db(tmp_path)
        # Assert — was deleted and re-created with sentinel
        assert discord_path.exists()
        assert _has_sentinel(discord_path)

    def test_ensure_discord_db_complete_noop(self, tmp_path: Path) -> None:
        # Arrange — discord.db already complete
        discord_path = tmp_path / "discord.db"
        _create_db_with_sentinel(discord_path)
        mtime_before = discord_path.stat().st_mtime
        # Act
        _ensure_discord_db(tmp_path)
        # Assert — untouched
        assert discord_path.stat().st_mtime == mtime_before
