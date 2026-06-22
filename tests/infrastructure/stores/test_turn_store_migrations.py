"""Tests for TurnStore v5 schema migration — cwd column on pool_sessions.

RED phase for issue #1777 (single SSoT for last-session).

The v5 migration must add a nullable TEXT column ``cwd`` to ``pool_sessions``,
guarded by the same ``'duplicate column'`` try/except pattern as the v4
``cli_session_id`` migration at turn_store.py:125-137.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from factory.infrastructure.stores.session.turn_store import TurnStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _pragma_col_names(store: TurnStore, table: str) -> list[str]:
    """Return column names for *table* via PRAGMA table_info."""
    db = store._db_or_raise()
    async with db.execute(f"PRAGMA table_info('{table}')") as cur:
        rows = await cur.fetchall()
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
    return [row[1] for row in rows]


# ---------------------------------------------------------------------------
# TestTurnStoreMigrationsV5
# ---------------------------------------------------------------------------


class TestTurnStoreMigrationsV5:
    """v5 migration: pool_sessions gains a nullable TEXT ``cwd`` column."""

    @pytest.mark.asyncio
    async def test_connect_adds_cwd_column_to_pool_sessions(self) -> None:
        """After connect(), pool_sessions must have a ``cwd`` column (TEXT, nullable).

        Negative: deleting the v5 ALTER TABLE statement from connect() causes
        this test to fail — ``cwd`` is absent from the schema, so the assertion
        ``"cwd" in cols`` is False and the test correctly catches the regression.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "turns.db"
            store = TurnStore(db_path)
            await store.connect()
            try:
                cols = await _pragma_col_names(store, "pool_sessions")
                assert "cwd" in cols, (
                    f"pool_sessions is missing the 'cwd' column; "
                    f"present columns: {cols}"
                )
            finally:
                await store.close()

    @pytest.mark.asyncio
    async def test_connect_cwd_migration_is_idempotent(self) -> None:
        """Calling connect() twice on the same DB file must not raise.

        The v5 ALTER TABLE must be wrapped in a try/except that swallows
        ``sqlite3.OperationalError`` when the message contains
        ``'duplicate column'``, matching the v4 ``cli_session_id`` guard at
        turn_store.py:125-137.

        Negative: removing the ``'duplicate column'`` guard causes the second
        connect() to propagate ``OperationalError("duplicate column name: cwd")``
        and this test fails.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "turns.db"

            # First connect — creates the schema + runs v5 migration.
            store1 = TurnStore(db_path)
            await store1.connect()
            await store1.close()

            # Second connect on the same file — must not raise.
            store2 = TurnStore(db_path)
            await store2.connect()  # raises OperationalError if guard is absent
            await store2.close()
