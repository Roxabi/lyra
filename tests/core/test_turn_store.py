"""Tests for TurnStore — L1 raw turn logging (issue #67)."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.infrastructure.stores.turn_store import TurnStore


@pytest.fixture
async def store(tmp_path):
    """In-memory TurnStore for tests."""
    s = TurnStore(":memory:")
    await s.connect()
    yield s
    await s.close()


class TestTurnStoreSchema:
    async def test_connect_creates_table(self, store: TurnStore) -> None:
        """connect() must create the conversation_turns table."""
        db = store._db_or_raise()
        async with db.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type='table' AND name='conversation_turns'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None

    async def test_connect_creates_indices(self, store: TurnStore) -> None:
        """connect() must create both session and pool indices."""
        db = store._db_or_raise()
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
            " AND name IN ('idx_turns_session', 'idx_turns_pool')"
        ) as cur:
            rows = list(await cur.fetchall())
        assert len(rows) == 2

    async def test_idempotent_migration(self, tmp_path) -> None:
        """connect() on an existing database must not raise."""
        path = tmp_path / "turns.db"
        s = TurnStore(path)
        await s.connect()
        await s.close()
        s2 = TurnStore(path)
        await s2.connect()
        await s2.close()


class TestTurnStoreLogTurn:
    async def test_log_user_turn(self, store: TurnStore) -> None:
        """log_turn persists a user turn with correct fields."""
        await store.log_turn(
            pool_id="telegram:main:chat:1",
            session_id="sess-abc",
            role="user",
            platform="telegram",
            user_id="u123",
            content="hello",
            message_id="msg-1",
        )
        rows = await store.get_turns("telegram:main:chat:1", user_id="u123")
        assert len(rows) == 1
        row = rows[0]
        assert row["role"] == "user"
        assert row["content"] == "hello"
        assert row["message_id"] == "msg-1"
        assert row["reply_message_id"] is None
        assert row["session_id"] == "sess-abc"
        assert row["user_id"] == "u123"

    async def test_log_assistant_turn(self, store: TurnStore) -> None:
        """log_turn persists an assistant turn with reply_message_id."""
        await store.log_turn(
            pool_id="telegram:main:chat:1",
            session_id="sess-abc",
            role="assistant",
            platform="telegram",
            user_id="u123",
            content="hello back",
            reply_message_id="reply-42",
        )
        rows = await store.get_turns("telegram:main:chat:1", user_id="u123")
        assert rows[0]["role"] == "assistant"
        assert rows[0]["reply_message_id"] == "reply-42"
        assert rows[0]["message_id"] is None

    async def test_log_multiple_turns(self, store: TurnStore) -> None:
        """Multiple turns are all persisted."""
        for i in range(5):
            await store.log_turn(
                pool_id="pool:1",
                session_id="sess",
                role="user" if i % 2 == 0 else "assistant",
                platform="discord",
                user_id="u1",
                content=f"msg {i}",
            )
        rows = await store.get_turns("pool:1", user_id="u1")
        assert len(rows) == 5

    async def test_get_turns_newest_first(self, store: TurnStore) -> None:
        """get_turns returns rows ordered newest-first (ORDER BY timestamp DESC)."""
        contents = ["first", "second", "third"]
        for c in contents:
            await store.log_turn(
                pool_id="pool:ord",
                session_id="s",
                role="user",
                platform="telegram",
                user_id="u",
                content=c,
            )
            await asyncio.sleep(0.01)  # ensure distinct timestamps
        rows = await store.get_turns("pool:ord", user_id="u")
        assert rows[0]["content"] == "third"
        assert rows[-1]["content"] == "first"

    async def test_get_turns_scoped_to_pool(self, store: TurnStore) -> None:
        """get_turns returns only turns for the requested pool_id."""
        await store.log_turn(
            pool_id="pool:A",
            session_id="s",
            role="user",
            platform="telegram",
            user_id="u",
            content="A",
        )
        await store.log_turn(
            pool_id="pool:B",
            session_id="s",
            role="user",
            platform="telegram",
            user_id="u",
            content="B",
        )
        assert len(await store.get_turns("pool:A", user_id="u")) == 1
        assert len(await store.get_turns("pool:B", user_id="u")) == 1

    async def test_get_turns_scoped_to_user(self, store: TurnStore) -> None:
        """get_turns filters by user_id — cross-user reads return no rows."""
        await store.log_turn(
            pool_id="pool:1",
            session_id="s",
            role="user",
            platform="telegram",
            user_id="owner",
            content="private",
        )
        # Different user cannot read owner's turns via same pool_id.
        rows = await store.get_turns("pool:1", user_id="intruder")
        assert rows == []

    async def test_get_turns_respects_limit(self, store: TurnStore) -> None:
        """get_turns(limit=N) returns at most N rows."""
        for _ in range(10):
            await store.log_turn(
                pool_id="pool:1",
                session_id="s",
                role="user",
                platform="cli",
                user_id="u",
                content="x",
            )
        rows = await store.get_turns("pool:1", user_id="u", limit=3)
        assert len(rows) == 3

    async def test_get_turns_limit_capped(self, store: TurnStore) -> None:
        """get_turns silently caps limit at 500 — no error raised."""
        rows = await store.get_turns("pool:none", user_id="u", limit=999_999)
        assert rows == []  # empty pool, but no error from oversized limit

    async def test_get_turns_empty_pool(self, store: TurnStore) -> None:
        """get_turns returns [] for an unknown pool."""
        assert await store.get_turns("pool:unknown", user_id="u") == []

    async def test_log_turn_with_metadata(self, store: TurnStore) -> None:
        """metadata dict is stored as JSON and deserialized on read."""
        await store.log_turn(
            pool_id="p",
            session_id="s",
            role="user",
            platform="telegram",
            user_id="u",
            content="hi",
            metadata={"foo": "bar", "n": 42},
        )
        rows = await store.get_turns("p", user_id="u")
        # metadata is deserialized — returned as dict, not JSON string.
        assert rows[0]["metadata"] == {"foo": "bar", "n": 42}


class TestTurnStoreErrors:
    async def test_db_or_raise_before_connect(self) -> None:
        """_db_or_raise() raises RuntimeError if not connected."""
        s = TurnStore(":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            s._db_or_raise()

    async def test_close_idempotent(self) -> None:
        """close() called twice must not raise."""
        s = TurnStore(":memory:")
        await s.connect()
        await s.close()
        await s.close()  # second close — no-op

    async def test_invalid_role_raises(self, store: TurnStore) -> None:
        """log_turn raises ValueError for an unrecognized role."""
        with pytest.raises(ValueError, match="invalid role"):
            await store.log_turn(
                pool_id="p",
                session_id="s",
                role="system",
                platform="telegram",
                user_id="u",
                content="hi",
            )


class TestTurnStoreIntegrationWithPool:
    def _make_msg(
        self,
        user_id: str = "u1",
        platform: str = "telegram",
        text: str = "hello",
        msg_id: str = "m1",
    ):
        from lyra.core.message import InboundMessage
        from lyra.core.trust import TrustLevel

        return InboundMessage(
            id=msg_id,
            platform=platform,
            bot_id="main",
            scope_id="chat:1",
            user_id=user_id,
            user_name="User",
            is_mention=False,
            text=text,
            text_raw=text,
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
        )

    def _make_ctx(self):
        ctx = MagicMock()
        ctx.get_agent = MagicMock(return_value=None)
        ctx.get_message = MagicMock(return_value=None)
        ctx.dispatch_response = AsyncMock(return_value=None)
        ctx.dispatch_streaming = AsyncMock(return_value=None)
        ctx.record_circuit_success = MagicMock()
        ctx.record_circuit_failure = MagicMock()
        return ctx

    async def test_pool_append_logs_user_turn(self) -> None:
        """When _turn_store is set, append() fires a task to log the user turn."""
        from lyra.core.pool import Pool

        store = TurnStore(":memory:")
        await store.connect()
        pool = Pool(pool_id="p:1", agent_name="a", ctx=self._make_ctx())
        pool._turn_store = store
        msg = self._make_msg()

        await pool.append(msg)

        rows = await store.get_turns("p:1", user_id="u1")
        assert len(rows) == 1
        assert rows[0]["role"] == "user"
        assert rows[0]["content"] == "hello"
        assert rows[0]["message_id"] == "m1"

        await store.close()

    async def test_process_one_logs_assistant_turn(self) -> None:
        """_process_one() logs an assistant turn after dispatching a Response."""
        from lyra.core.message import OutboundMessage, Response
        from lyra.core.pool import Pool

        store = TurnStore(":memory:")
        await store.connect()

        ctx = self._make_ctx()

        # Simulate real dispatch: invoke _on_dispatched callback (#316).
        async def _dispatch_with_callback(msg, response):
            if isinstance(response, Response):
                _cb = response.metadata.get("_on_dispatched")
                if _cb is not None:
                    outbound = OutboundMessage.from_text(response.content)
                    outbound.metadata["reply_message_id"] = "bot_msg_42"
                    result = _cb(outbound)
                    if inspect.isawaitable(result):
                        await result

        ctx.dispatch_response = AsyncMock(side_effect=_dispatch_with_callback)

        # Stub agent that returns a Response
        agent = MagicMock()
        agent.name = "stub"
        agent.process = AsyncMock(return_value=Response(content="hi there"))
        agent.is_backend_alive = MagicMock(return_value=True)
        agent._ensure_system_prompt = AsyncMock(return_value=None)
        agent.compact = AsyncMock(return_value=None)
        ctx.get_agent = MagicMock(return_value=agent)

        pool = Pool(pool_id="p:2", agent_name="stub", ctx=ctx)
        pool._turn_store = store

        from lyra.core.pool.pool_processor_exec import process_one

        msg = self._make_msg(user_id="u2")
        await process_one(msg, agent, pool)
        await asyncio.sleep(0.05)

        rows = await store.get_turns("p:2", user_id="u2")
        roles = {r["role"] for r in rows}
        assert "user" in roles
        assert "assistant" in roles
        assistant_row = next(r for r in rows if r["role"] == "assistant")
        assert assistant_row["content"] == "hi there"
        assert assistant_row["reply_message_id"] == "bot_msg_42"

        await store.close()

    async def test_concurrent_writes_two_pools(self) -> None:
        """Concurrent log_turn calls from two pools both persist successfully."""
        store = TurnStore(":memory:")
        await store.connect()

        await asyncio.gather(
            store.log_turn(
                pool_id="pool:A",
                session_id="s",
                role="user",
                platform="telegram",
                user_id="uA",
                content="from A",
            ),
            store.log_turn(
                pool_id="pool:B",
                session_id="s",
                role="user",
                platform="telegram",
                user_id="uB",
                content="from B",
            ),
        )

        assert len(await store.get_turns("pool:A", user_id="uA")) == 1
        assert len(await store.get_turns("pool:B", user_id="uB")) == 1

        await store.close()


class TestPoolSessions:
    async def test_start_session_idempotent(self, store: TurnStore) -> None:
        """start_session called twice with the same session_id inserts only 1 row."""
        await store.start_session("sess-idem", "pool:idem")
        await store.start_session("sess-idem", "pool:idem")

        db = store._db_or_raise()
        async with db.execute(
            "SELECT COUNT(*) FROM pool_sessions WHERE session_id = ?",
            ("sess-idem",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 1

    async def test_get_last_session_from_pool_sessions(self, store: TurnStore) -> None:
        """get_last_session returns the session registered via start_session."""
        await store.start_session("sess-latest", "pool:q")
        result = await store.get_last_session("pool:q")
        assert result == "sess-latest"

    async def test_get_last_session_returns_none_for_unknown_pool(
        self, store: TurnStore
    ) -> None:
        """get_last_session returns None when no sessions exist for the pool."""
        result = await store.get_last_session("pool:nonexistent")
        assert result is None

    async def test_backfill_no_duplicates(self, store: TurnStore) -> None:
        """_backfill_sessions run twice produces exactly one row per session."""
        db = store._db_or_raise()
        # Insert turns directly to simulate pre-existing history.
        ts = "2025-01-01T00:00:00+00:00"
        await db.execute(
            "INSERT INTO conversation_turns"
            " (pool_id, session_id, role, platform, user_id, content, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("pool:bf", "sess-bf", "user", "telegram", "u", "hi", ts),
        )
        await db.commit()

        from lyra.infrastructure.stores.turn_store_queries import backfill_sessions

        await backfill_sessions(db)
        await backfill_sessions(db)  # second call must be a no-op

        async with db.execute(
            "SELECT COUNT(*) FROM pool_sessions WHERE session_id = ?",
            ("sess-bf",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 1

    async def test_log_turn_updates_last_active_at(self, store: TurnStore) -> None:
        """log_turn updates last_active_at on the matching pool_sessions row."""
        await store.start_session("sess-ts", "pool:ts")

        db = store._db_or_raise()
        async with db.execute(
            "SELECT last_active_at FROM pool_sessions WHERE session_id = ?",
            ("sess-ts",),
        ) as cur:
            _row = await cur.fetchone()
            assert _row is not None
            before = _row[0]

        # Small sleep to guarantee the timestamp advances.
        await asyncio.sleep(0.01)

        await store.log_turn(
            pool_id="pool:ts",
            session_id="sess-ts",
            role="user",
            platform="telegram",
            user_id="u",
            content="hello",
        )

        async with db.execute(
            "SELECT last_active_at FROM pool_sessions WHERE session_id = ?",
            ("sess-ts",),
        ) as cur:
            _row = await cur.fetchone()
            assert _row is not None
            after = _row[0]

        assert after > before

    async def test_get_last_session_returns_most_recent(self, store: TurnStore) -> None:
        """get_last_session returns the most recently started session, not first."""
        # Arrange — register two sessions with a measurable time gap
        await store.start_session("sess-first", "pool:order")
        await asyncio.sleep(0.01)  # ensure distinct last_active_at timestamps
        await store.start_session("sess-second", "pool:order")

        # Act
        result = await store.get_last_session("pool:order")

        # Assert — second session has a later last_active_at, so it wins
        assert result == "sess-second"


class TestBackfillOnFirstConnect:
    async def test_backfill_on_first_connect(self, tmp_path) -> None:
        """connect() backfills pool_sessions from pre-existing conversation_turns rows.

        Simulates a DB that was written before pool_sessions existed (#417):
        raw turns are inserted directly via sqlite3, then TurnStore.connect()
        is called.  The backfill must derive pool_sessions with
        started_at = MIN(timestamp) for each (pool_id, session_id) group.
        """
        # Arrange — create turns.db with raw turns (no pool_sessions yet)
        db_path = tmp_path / "turns.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE conversation_turns (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_id           TEXT    NOT NULL,
                session_id        TEXT    NOT NULL,
                role              TEXT    NOT NULL,
                platform          TEXT    NOT NULL,
                user_id           TEXT    NOT NULL,
                content           TEXT    NOT NULL,
                message_id        TEXT,
                reply_message_id  TEXT,
                timestamp         TEXT    NOT NULL,
                metadata          TEXT    DEFAULT '{}'
            )
            """
        )
        # Insert two turns in the same session with different timestamps
        ts_early = "2025-01-01T10:00:00+00:00"
        ts_late = "2025-01-01T11:00:00+00:00"
        conn.execute(
            "INSERT INTO conversation_turns"
            " (pool_id, session_id, role, platform, user_id, content, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("pool:bf", "sess-bf", "user", "telegram", "u", "first", ts_early),
        )
        conn.execute(
            "INSERT INTO conversation_turns"
            " (pool_id, session_id, role, platform, user_id, content, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("pool:bf", "sess-bf", "assistant", "telegram", "u", "second", ts_late),
        )
        conn.commit()
        conn.close()

        # Act — TurnStore.connect() must run the backfill
        store = TurnStore(db_path)
        await store.connect()

        # Assert — pool_sessions row was created with started_at = MIN(timestamp)
        db = store._db_or_raise()
        async with db.execute(
            "SELECT session_id, pool_id, started_at FROM pool_sessions"
            " WHERE session_id = ?",
            ("sess-bf",),
        ) as cur:
            row = await cur.fetchone()

        assert row is not None
        session_id, pool_id, started_at = row
        assert session_id == "sess-bf"
        assert pool_id == "pool:bf"
        assert started_at == ts_early  # MIN(timestamp) = earliest turn

        await store.close()
