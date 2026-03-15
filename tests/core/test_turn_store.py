"""Tests for TurnStore — L1 raw turn logging (issue #67)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from lyra.core.turn_store import TurnStore


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
        path = tmp_path / "vault.db"
        s = TurnStore(path)
        await s.connect()
        await s.close()
        # Second connect — should not fail
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
        rows = await store.get_turns("telegram:main:chat:1")
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
        rows = await store.get_turns("telegram:main:chat:1")
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
        rows = await store.get_turns("pool:1")
        assert len(rows) == 5

    async def test_get_turns_scoped_to_pool(self, store: TurnStore) -> None:
        """get_turns returns only turns for the requested pool_id."""
        await store.log_turn(
            pool_id="pool:A", session_id="s", role="user",
            platform="telegram", user_id="u", content="A",
        )
        await store.log_turn(
            pool_id="pool:B", session_id="s", role="user",
            platform="telegram", user_id="u", content="B",
        )
        assert len(await store.get_turns("pool:A")) == 1
        assert len(await store.get_turns("pool:B")) == 1

    async def test_get_turns_respects_limit(self, store: TurnStore) -> None:
        """get_turns(limit=N) returns at most N rows."""
        for _ in range(10):
            await store.log_turn(
                pool_id="pool:1", session_id="s", role="user",
                platform="cli", user_id="u", content="x",
            )
        rows = await store.get_turns("pool:1", limit=3)
        assert len(rows) == 3

    async def test_get_turns_empty_pool(self, store: TurnStore) -> None:
        """get_turns returns [] for an unknown pool."""
        assert await store.get_turns("pool:unknown") == []

    async def test_log_turn_with_metadata(self, store: TurnStore) -> None:
        """metadata dict is stored as JSON and round-trips correctly."""
        import json

        await store.log_turn(
            pool_id="p", session_id="s", role="user",
            platform="telegram", user_id="u", content="hi",
            metadata={"foo": "bar", "n": 42},
        )
        rows = await store.get_turns("p")
        meta = json.loads(rows[0]["metadata"])
        assert meta == {"foo": "bar", "n": 42}


class TestTurnStoreErrors:
    async def test_db_or_raise_before_connect(self) -> None:
        """_db_or_raise() raises RuntimeError if not connected."""
        s = TurnStore(":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            s._db_or_raise()


class TestTurnStoreIntegrationWithPool:
    async def test_pool_append_logs_user_turn(self) -> None:
        """When _turn_store is set, append() fires a task to log the user turn."""
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        from lyra.core.message import InboundMessage
        from lyra.core.pool import Pool
        from lyra.core.trust import TrustLevel

        store = TurnStore(":memory:")
        await store.connect()

        ctx = MagicMock()
        ctx.get_agent = MagicMock(return_value=None)
        ctx.get_message = MagicMock(return_value=None)
        ctx.dispatch_response = AsyncMock(return_value=None)
        ctx.dispatch_streaming = AsyncMock(return_value=None)
        ctx.record_circuit_success = MagicMock()
        ctx.record_circuit_failure = MagicMock()

        pool = Pool(pool_id="p:1", agent_name="a", ctx=ctx)
        pool._turn_store = store

        msg = InboundMessage(
            id="m1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:1",
            user_id="u1",
            user_name="User",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
        )

        pool.append(msg)
        # Allow the fire-and-forget task to complete
        await asyncio.sleep(0.05)

        rows = await store.get_turns("p:1")
        assert len(rows) == 1
        assert rows[0]["role"] == "user"
        assert rows[0]["content"] == "hello"
        assert rows[0]["message_id"] == "m1"

        await store.close()
