"""SQLite migration guards for multibot bootstrap store opening (#417, #2001)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import aiosqlite

from factory.core.agent.schema.bot_schema import _CREATE_BOTS
from factory.infrastructure.stores.identity.agent_grant_store import (
    _CREATE_AGENT_GRANTS,
)
from factory.infrastructure.stores.identity.auth_store import (
    _CREATE_GRANTS as _CREATE_AUTH_GRANTS,
)
from factory.infrastructure.stores.identity.identity_alias_store import (
    _CREATE_ALIASES,
    _CREATE_CHALLENGES,
)
from factory.infrastructure.stores.identity.user_store import (
    _CREATE_PLATFORM_IDENTITIES,
    _CREATE_USER_MIGRATION,
    _CREATE_USERS,
    ensure_users_email_schema,
)
from factory.infrastructure.stores.migrations.bot_store_migrations import (
    run_bot_migrations,
)

log = logging.getLogger(__name__)

_CONFIG_TABLES = (
    "agents",
    "bot_agent_map",
    "agent_runtime_state",
    "bot_secrets",
    "user_prefs",
)

_AUTH_DB_DDL: tuple[str, ...] = (
    _CREATE_AUTH_GRANTS,
    _CREATE_ALIASES,
    _CREATE_CHALLENGES,
    _CREATE_AGENT_GRANTS,
    _CREATE_USERS,
    _CREATE_PLATFORM_IDENTITIES,
    _CREATE_USER_MIGRATION,
)

_SENTINEL_DDL = (
    "CREATE TABLE IF NOT EXISTS _migration_complete (migrated_at TEXT NOT NULL)"
)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DDL_INDEX_RE = re.compile(
    r"^CREATE\s+(UNIQUE\s+)?INDEX\s+"
    r"(IF\s+NOT\s+EXISTS\s+)?[A-Za-z_][A-Za-z0-9_]*\s+ON\b",
    re.IGNORECASE,
)


def _copy_indices(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    tables: tuple[str, ...],
) -> None:
    """Copy all indices for *tables* from *src* to *dst*, skipping duplicates."""
    idx_rows = src.execute(
        "SELECT sql FROM sqlite_master"
        " WHERE type='index' AND sql IS NOT NULL AND tbl_name IN (%s)"
        % ",".join("?" for _ in tables),
        tables,
    ).fetchall()
    for (idx_sql,) in idx_rows:
        if not _DDL_INDEX_RE.match(idx_sql):
            log.warning("Skipping unexpected DDL from sqlite_master: %r", idx_sql[:80])
            continue
        try:
            dst.execute(idx_sql)
        except sqlite3.OperationalError:
            pass  # index may already exist


def _has_sentinel(db_path: Path) -> bool:
    """Check whether config.db has the _migration_complete sentinel."""
    if not db_path.exists():
        return False
    try:
        with closing(sqlite3.connect(str(db_path))) as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type='table' AND name='_migration_complete'"
            )
            if not cur.fetchone():
                return False
            cur = conn.execute("SELECT 1 FROM _migration_complete LIMIT 1")
            return cur.fetchone() is not None
    except sqlite3.Error:
        return False


def _atomic_table_copy(  # noqa: C901 — DEBT:migration-sequence-bootstrap — sequential migration steps
    src_path: Path,
    dst_path: Path,
    tables: tuple[str, ...],
    tmp_prefix: str,
) -> int:
    """Copy *tables* from *src_path* to *dst_path* (synchronous, atomic).

    Writes to a temp file first, then renames atomically. Column/table names
    are validated before SQL interpolation. Returns total rows copied.
    """
    if not src_path.exists():
        log.warning("%s not found — skipping migration", src_path)
        return 0

    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(src_path.parent), prefix=tmp_prefix, suffix=".db"
    )
    tmp_path = Path(tmp_path_str)
    src: sqlite3.Connection | None = None
    dst: sqlite3.Connection | None = None
    total_rows = 0
    _success = False
    try:
        os.close(fd)
        src = sqlite3.connect(str(src_path))
        dst = sqlite3.connect(str(tmp_path))
        dst.execute("PRAGMA journal_mode=WAL")

        for table in tables:
            if not _IDENT_RE.match(table):
                raise ValueError(f"Invalid table name: {table!r}")
            row = src.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if row is None:
                log.debug("Table %s not found in %s — skipping", table, src_path.name)
                continue
            dst.execute(row[0])
            cols_cur = src.execute(f"PRAGMA table_info({table})")
            col_names = [r[1] for r in cols_cur.fetchall()]
            for c in col_names:
                if not _IDENT_RE.match(c):
                    raise ValueError(f"Invalid column name in {table}: {c!r}")
            col_list = ", ".join(col_names)
            placeholders = ", ".join("?" for _ in col_names)
            rows = src.execute(f"SELECT {col_list} FROM {table}").fetchall()
            if rows:
                dst.executemany(
                    f"INSERT OR IGNORE INTO {table} ({col_list})"
                    f" VALUES ({placeholders})",
                    rows,
                )
            total_rows += len(rows)
            log.debug("Migrated %d rows for table %s", len(rows), table)

        _copy_indices(src, dst, tables)

        dst.execute(_SENTINEL_DDL)
        dst.execute(
            "INSERT INTO _migration_complete (migrated_at) VALUES (datetime('now'))"
        )
        dst.commit()
        _success = True
    finally:
        if src is not None:
            src.close()
        if dst is not None:
            dst.close()
        if not _success:
            tmp_path.unlink(missing_ok=True)

    shutil.move(str(tmp_path), str(dst_path))
    return total_rows


def _migrate_to_config_db(vault_dir: Path) -> None:
    """Copy config tables from auth.db → config.db (atomic rename)."""
    n = _atomic_table_copy(
        src_path=vault_dir / "auth.db",
        dst_path=vault_dir / "config.db",
        tables=_CONFIG_TABLES,
        tmp_prefix=".config_db_migrate_",
    )
    if n:
        log.warning("auth.db split: migrated %d rows to config.db (tombstones kept)", n)


def _migrate_threads_to_discord_db(vault_dir: Path) -> None:
    """Copy discord_threads from auth.db → discord.db (atomic rename)."""
    n = _atomic_table_copy(
        src_path=vault_dir / "auth.db",
        dst_path=vault_dir / "discord.db",
        tables=("discord_threads",),
        tmp_prefix=".discord_db_migrate_",
    )
    if n:
        log.warning("ThreadStore migration: moved %d rows to discord.db", n)


def _ensure_discord_db(vault_dir: Path) -> None:
    """Migration guard: ensure discord.db exists for ThreadStore (#417 / S4)."""
    discord_path = vault_dir / "discord.db"

    if discord_path.exists() and not _has_sentinel(discord_path):
        log.warning("Partial discord.db detected — deleting and re-migrating")
        discord_path.unlink()

    if not discord_path.exists():
        _migrate_threads_to_discord_db(vault_dir)


def _ensure_config_db(vault_dir: Path) -> None:
    """Migration guard: ensure config.db exists and is complete."""
    config_path = vault_dir / "config.db"

    if config_path.exists() and not _has_sentinel(config_path):
        log.warning("Partial config.db detected — deleting and re-migrating")
        config_path.unlink()

    if not config_path.exists():
        _migrate_to_config_db(vault_dir)


def _ensure_auth_db_schema(vault_dir: Path) -> None:
    """Ensure every auth.db table exists before multi-connection store opens."""
    auth_path = vault_dir / "auth.db"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(auth_path), timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        for stmt in _AUTH_DB_DDL:
            conn.execute(stmt)
        ensure_users_email_schema(conn)
        alias_count = conn.execute(
            "SELECT COUNT(*) FROM identity_aliases"
        ).fetchone()[0]
        if alias_count == 0:
            conn.execute(
                "INSERT INTO _user_store_migration (migrated_at) "
                "SELECT datetime('now') "
                "WHERE NOT EXISTS (SELECT 1 FROM _user_store_migration)"
            )
        conn.commit()
    finally:
        conn.close()


async def _ensure_config_db_bot_migrations(vault_dir: Path) -> None:
    """Run BotStore migrations on a short-lived connection before stores open."""
    config_path = vault_dir / "config.db"
    if not config_path.exists():
        return
    async with aiosqlite.connect(str(config_path)) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute(_CREATE_BOTS)
        await db.commit()
        await run_bot_migrations(db)