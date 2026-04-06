"""SqliteStore WAL checkpoint — periodic task and checkpoint-on-close."""

from __future__ import annotations

import asyncio
from pathlib import Path

from lyra.core.stores.sqlite_base import (
    SqliteStore,
)

# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------


class _SimpleStore(SqliteStore):
    """Concrete subclass with a trivial schema."""

    async def connect(self) -> None:
        await self._open_db(
            ["CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY)"]
        )


# ---------------------------------------------------------------------------
# TestWalCheckpointOnClose
# ---------------------------------------------------------------------------


class TestWalCheckpointOnClose:
    """close() must checkpoint WAL before closing the connection."""

    async def test_checkpoint_on_close_runs_without_error(self, tmp_path: Path) -> None:
        store = _SimpleStore(tmp_path / "test.db")
        await store.connect()
        # Should not raise — even on an empty WAL
        await store.close()
        assert store._db is None

    async def test_close_is_idempotent(self, tmp_path: Path) -> None:
        store = _SimpleStore(tmp_path / "test.db")
        await store.connect()
        await store.close()
        # Second close must not raise
        await store.close()
        assert store._db is None

    async def test_checkpoint_task_cancelled_on_close(self, tmp_path: Path) -> None:
        store = _SimpleStore(tmp_path / "test.db")
        await store.connect()
        task = store._checkpoint_task
        assert task is not None
        assert not task.done()
        await store.close()
        assert task.done()
        assert store._checkpoint_task is None

    async def test_wal_file_truncated_after_close(self, tmp_path: Path) -> None:
        """WAL file should be at zero or minimal size after TRUNCATE checkpoint."""
        db_path = tmp_path / "test.db"
        wal_path = tmp_path / "test.db-wal"

        store = _SimpleStore(db_path)
        await store.connect()
        # Write enough rows to produce WAL pages
        db = store._require_db()
        for i in range(50):
            await db.execute("INSERT INTO items VALUES (?)", (i,))
        await db.commit()

        # WAL should exist with some content now
        assert wal_path.exists()

        await store.close()

        # After TRUNCATE checkpoint the WAL should be absent or empty
        wal_size = wal_path.stat().st_size if wal_path.exists() else 0
        assert wal_size == 0, f"WAL not truncated after close: {wal_size} bytes"


# ---------------------------------------------------------------------------
# TestPeriodicCheckpointTask
# ---------------------------------------------------------------------------


class TestPeriodicCheckpointTask:
    """Background checkpoint task lifecycle."""

    async def test_checkpoint_task_started_on_open_db(self, tmp_path: Path) -> None:
        store = _SimpleStore(tmp_path / "test.db")
        await store.connect()
        try:
            assert store._checkpoint_task is not None
            assert not store._checkpoint_task.done()
        finally:
            await store.close()

    async def test_checkpoint_task_not_started_before_connect(
        self, tmp_path: Path
    ) -> None:
        store = _SimpleStore(tmp_path / "test.db")
        assert store._checkpoint_task is None

    async def test_open_db_idempotent_does_not_spawn_second_task(
        self, tmp_path: Path
    ) -> None:
        """Calling _open_db() twice must not create a second background task."""
        store = _SimpleStore(tmp_path / "test.db")
        await store.connect()
        try:
            task_after_first = store._checkpoint_task
            # Simulate accidental second call
            await store._open_db()
            assert store._checkpoint_task is task_after_first
        finally:
            await store.close()

    async def test_manual_checkpoint_runs_without_error(self, tmp_path: Path) -> None:
        store = _SimpleStore(tmp_path / "test.db")
        await store.connect()
        try:
            await store._checkpoint()
        finally:
            await store.close()

    async def test_short_interval_checkpoint_fires(self, tmp_path: Path) -> None:
        """With a 0-second interval the checkpoint runs immediately on next tick."""

        class _FastStore(_SimpleStore):
            _wal_checkpoint_interval = 0

        store = _FastStore(tmp_path / "fast.db")
        await store.connect()
        try:
            # Let the event loop tick so the task can run
            await asyncio.sleep(0.05)
            # If checkpoint raised, the task would be in an exception state
            assert store._checkpoint_task is not None
            assert not store._checkpoint_task.done()
        finally:
            await store.close()
