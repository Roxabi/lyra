"""Tests for MessageIndex store (#341)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lyra.infrastructure.stores.message_index import MessageIndex


@pytest.fixture
async def store(tmp_path):
    s = MessageIndex(db_path=tmp_path / "message_index.db")
    await s.connect()
    yield s
    await s.close()


class TestMessageIndex:
    """Unit tests for MessageIndex store."""

    async def test_connect_creates_table(self, tmp_path):
        s = MessageIndex(db_path=tmp_path / "mi.db")
        await s.connect()
        db = s._require_db()
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_index'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        await s.close()

    async def test_upsert_and_resolve(self, store: MessageIndex):
        await store.upsert("pool:tg:main", "msg-123", "sess-abc", "user")
        result = await store.resolve("pool:tg:main", "msg-123")
        assert result == "sess-abc"

    async def test_upsert_normalizes_to_str(self, store: MessageIndex):
        # Simulate Telegram int message_id passed as int-like string
        await store.upsert("pool:tg:main", "12345", "sess-xyz", "user")
        result = await store.resolve("pool:tg:main", "12345")
        assert result == "sess-xyz"

    async def test_upsert_skips_none_msg_id(self, store: MessageIndex):
        # Circuit-breaker guard: None platform_msg_id should be skipped
        await store.upsert("pool:tg:main", None, "sess-abc", "assistant")
        # No row inserted — resolve should return None for any lookup
        result = await store.resolve("pool:tg:main", "None")
        assert result is None

    async def test_upsert_ignore_on_conflict(self, store: MessageIndex):
        # INSERT OR IGNORE preserves the original session mapping
        await store.upsert("pool:tg:main", "msg-1", "sess-first", "user")
        await store.upsert("pool:tg:main", "msg-1", "sess-second", "user")
        result = await store.resolve("pool:tg:main", "msg-1")
        assert result == "sess-first"

    async def test_resolve_not_found(self, store: MessageIndex):
        result = await store.resolve("pool:tg:main", "nonexistent")
        assert result is None

    async def test_resolve_scoped_by_pool_id(self, store: MessageIndex):
        await store.upsert("pool:tg:chat1", "msg-1", "sess-a", "user")
        await store.upsert("pool:tg:chat2", "msg-1", "sess-b", "user")
        assert await store.resolve("pool:tg:chat1", "msg-1") == "sess-a"
        assert await store.resolve("pool:tg:chat2", "msg-1") == "sess-b"

    async def test_cleanup_older_than(self, store: MessageIndex):
        # Insert a row, then cleanup with 0 days (deletes everything)
        await store.upsert("pool:tg:main", "msg-old", "sess-old", "user")
        deleted = await store.cleanup_older_than(0)
        assert deleted == 1
        assert await store.resolve("pool:tg:main", "msg-old") is None

    async def test_both_roles_indexed(self, store: MessageIndex):
        await store.upsert("pool:tg:main", "msg-user", "sess-1", "user")
        await store.upsert("pool:tg:main", "msg-bot", "sess-1", "assistant")
        assert await store.resolve("pool:tg:main", "msg-user") == "sess-1"
        assert await store.resolve("pool:tg:main", "msg-bot") == "sess-1"


class TestMessageIndexStartupPrune:
    """Tests for startup pruning behaviour (#417 / S1)."""

    async def test_cleanup_removes_old_keeps_recent(self, tmp_path):
        """Entries older than retention_days are deleted; recent ones survive."""
        s = MessageIndex(db_path=tmp_path / "mi_prune.db")
        await s.connect()

        old_ts = (datetime.now(UTC) - timedelta(days=91)).isoformat()
        recent_ts = (datetime.now(UTC) - timedelta(days=1)).isoformat()

        # Insert old entries directly to bypass upsert's _now_iso()
        db = s._require_db()
        _sql = (
            "INSERT INTO message_index"
            " (pool_id, platform_msg_id, session_id, role, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
        )
        await db.execute(
            _sql, ("pool:tg:main", "msg-old-1", "sess-old-1", "user", old_ts)
        )
        await db.execute(
            _sql, ("pool:tg:main", "msg-old-2", "sess-old-2", "assistant", old_ts)
        )
        # Insert a recent entry via direct SQL so we control its timestamp too
        await db.execute(
            _sql, ("pool:tg:main", "msg-recent", "sess-recent", "user", recent_ts)
        )
        await db.commit()

        pruned = await s.cleanup_older_than(90)

        assert pruned == 2
        assert await s.resolve("pool:tg:main", "msg-old-1") is None
        assert await s.resolve("pool:tg:main", "msg-old-2") is None
        assert await s.resolve("pool:tg:main", "msg-recent") == "sess-recent"

        await s.close()
