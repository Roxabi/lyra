"""Store lifecycle helpers for multibot bootstrap.

#417 — auth.db split: AgentStore, CredentialStore, PrefsStore now connect to
config.db.  ThreadStore connects to discord.db (owned by the Discord adapter
after S4 lands).  AuthStore remains on auth.db (grants only).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from lyra.core.stores.auth_store import AuthStore
from lyra.core.stores.identity_alias_store import IdentityAliasStore
from lyra.core.stores.message_index import MessageIndex
from lyra.core.stores.prefs_store import PrefsStore
from lyra.core.stores.turn_store import TurnStore
from lyra.infrastructure.stores.agent_store import AgentStore
from lyra.infrastructure.stores.credential_store import CredentialStore, LyraKeyring

log = logging.getLogger(__name__)

# Tables migrated from auth.db → config.db (#417 / S3)
_CONFIG_TABLES = (
    "agents",
    "bot_agent_map",
    "agent_runtime_state",
    "bot_secrets",
    "user_prefs",
)

_SENTINEL_DDL = (
    "CREATE TABLE IF NOT EXISTS _migration_complete (migrated_at TEXT NOT NULL)"
)


def _has_sentinel(db_path: Path) -> bool:
    """Check whether config.db has the _migration_complete sentinel."""
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(str(db_path)) as conn:
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


_IDENT_RE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _atomic_table_copy(  # noqa: C901 — sequential migration steps
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
            # Copy rows — validate column names before interpolation
            cols_cur = src.execute(f"PRAGMA table_info({table})")  # noqa: S608
            col_names = [r[1] for r in cols_cur.fetchall()]
            for c in col_names:
                if not _IDENT_RE.match(c):
                    raise ValueError(f"Invalid column name in {table}: {c!r}")
            col_list = ", ".join(col_names)
            placeholders = ", ".join("?" for _ in col_names)
            rows = src.execute(
                f"SELECT {col_list} FROM {table}"  # noqa: S608
            ).fetchall()
            if rows:
                dst.executemany(
                    f"INSERT OR IGNORE INTO {table} ({col_list})"  # noqa: S608
                    f" VALUES ({placeholders})",
                    rows,
                )
            total_rows += len(rows)
            log.debug("Migrated %d rows for table %s", len(rows), table)

        # Copy indices for migrated tables
        idx_rows = src.execute(
            "SELECT sql FROM sqlite_master"
            " WHERE type='index' AND sql IS NOT NULL AND tbl_name IN (%s)"
            % ",".join("?" for _ in tables),
            tables,
        ).fetchall()
        for (idx_sql,) in idx_rows:
            try:
                dst.execute(idx_sql)
            except sqlite3.OperationalError:
                pass  # index may already exist

        # Sentinel — marks migration as complete
        dst.execute(_SENTINEL_DDL)
        dst.execute(
            "INSERT INTO _migration_complete (migrated_at) VALUES (datetime('now'))"
        )
        dst.commit()
    finally:
        if src is not None:
            src.close()
        if dst is not None:
            dst.close()

    # Atomic rename (same filesystem)
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
    """Migration guard: ensure config.db exists and is complete.

    - Missing config.db → run migration from auth.db
    - Partial config.db (no sentinel) → delete and re-migrate
    - Complete config.db → no-op

    Note: there is a TOCTOU window between the existence check and the rename.
    Concurrent Lyra startups on the same vault_dir are not a supported scenario
    (single-instance deployment). The worst case is a duplicate migration that
    is non-destructive because INSERT OR IGNORE is used throughout.
    """
    config_path = vault_dir / "config.db"

    if config_path.exists() and not _has_sentinel(config_path):
        log.warning("Partial config.db detected — deleting and re-migrating")
        config_path.unlink()

    if not config_path.exists():
        _migrate_to_config_db(vault_dir)


@dataclass
class StoreBundle:
    """All persistent stores needed by the multibot bootstrap.

    ThreadStore is NOT included — owned by the Discord adapter (#417 / S4).
    """

    auth: AuthStore
    cred: CredentialStore
    agent: AgentStore
    turn: TurnStore
    prefs: PrefsStore
    message_index: MessageIndex
    identity_alias: IdentityAliasStore


@asynccontextmanager
async def open_stores(vault_dir: Path) -> AsyncIterator[StoreBundle]:
    """Open every store, yield a *StoreBundle*, and close on exit.

    Runs the auth.db → config.db migration guard before opening stores (#417).
    The finally block closes each store that was successfully opened,
    regardless of which later store (if any) failed to connect.
    """
    # Migration guards (#417)
    _ensure_config_db(vault_dir)
    _ensure_discord_db(vault_dir)

    auth_store: AuthStore | None = None
    cred_store: CredentialStore | None = None
    agent_store: AgentStore | None = None
    turn_store: TurnStore | None = None
    prefs_store: PrefsStore | None = None
    message_index_store: MessageIndex | None = None
    identity_alias_store: IdentityAliasStore | None = None
    try:
        auth_store = AuthStore(db_path=vault_dir / "auth.db")
        await auth_store.connect()

        identity_alias_store = IdentityAliasStore(db_path=vault_dir / "auth.db")
        await identity_alias_store.connect()

        keyring = LyraKeyring.load_or_create(vault_dir / "keyring.key")
        cred_store = CredentialStore(
            db_path=vault_dir / "config.db",
            keyring=keyring,
        )
        await cred_store.connect()

        agent_store = AgentStore(db_path=vault_dir / "config.db")
        await agent_store.connect()

        turn_store = TurnStore(db_path=vault_dir / "turns.db")
        await turn_store.connect()

        # ThreadStore is NOT opened here — owned by the Discord adapter (#417/S4)

        prefs_store = PrefsStore(db_path=vault_dir / "config.db")
        await prefs_store.connect()

        message_index_store = MessageIndex(db_path=vault_dir / "message_index.db")
        await message_index_store.connect()

        yield StoreBundle(
            auth=auth_store,
            cred=cred_store,
            agent=agent_store,
            turn=turn_store,
            prefs=prefs_store,
            message_index=message_index_store,
            identity_alias=identity_alias_store,
        )
    finally:
        all_stores = (
            cred_store,
            auth_store,
            agent_store,
            turn_store,
            prefs_store,
            message_index_store,
            identity_alias_store,
        )
        for store in all_stores:
            if store is not None:
                await store.close()
