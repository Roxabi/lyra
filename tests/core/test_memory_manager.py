"""Tests for MemoryManager and related data structures (issue #83 S2-S7).

RED phase — tests describe the MemoryManager API. All tests that exercise
the new memory module are expected to FAIL until the backend-dev GREEN phase
completes the implementation.

Spec trace: S2, S3, S4, S5, S6, S7
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# S2 — SessionSnapshot is a frozen dataclass
# ---------------------------------------------------------------------------


def test_session_snapshot_is_frozen():
    """SessionSnapshot must be immutable (frozen dataclass)."""
    from dataclasses import FrozenInstanceError

    from lyra.core.memory import SessionSnapshot

    snap = SessionSnapshot(
        session_id="s1",
        user_id="u1",
        medium="telegram",
        agent_namespace="lyra",
        session_start=datetime.now(UTC),
        session_end=datetime.now(UTC),
        message_count=3,
        source_turns=5,
    )
    with pytest.raises(FrozenInstanceError):
        snap.user_id = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mm():
    """Real MemoryManager with in-memory SQLite DB."""
    from lyra.core.memory import MemoryManager

    manager = MemoryManager(":memory:")
    await manager.connect()
    yield manager
    await manager.close()


@pytest.fixture
def make_snap():
    """Factory for SessionSnapshot test doubles."""

    def _make(
        session_id: str = "sess-1",
        user_id: str = "u1",
        medium: str = "telegram",
        agent_namespace: str = "lyra",
    ):
        from lyra.core.memory import SessionSnapshot

        return SessionSnapshot(
            session_id=session_id,
            user_id=user_id,
            medium=medium,
            agent_namespace=agent_namespace,
            session_start=datetime.now(UTC),
            session_end=datetime.now(UTC),
            message_count=3,
            source_turns=5,
        )

    return _make


# ---------------------------------------------------------------------------
# S2 — MemoryManager connect / close lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_calls_db_connect():
    """MemoryManager.connect() calls the underlying DB connect."""
    from lyra.core.memory import MemoryManager

    with patch("lyra.core.memory.AsyncMemoryDB") as MockDB:
        mock_db_instance = AsyncMock()
        MockDB.return_value = mock_db_instance

        manager = MemoryManager(":memory:")
        await manager.connect()

        mock_db_instance.connect.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_calls_db_close():
    """MemoryManager.close() calls the underlying DB close."""
    from lyra.core.memory import MemoryManager

    with patch("lyra.core.memory.AsyncMemoryDB") as MockDB:
        mock_db_instance = AsyncMock()
        MockDB.return_value = mock_db_instance

        manager = MemoryManager(":memory:")
        await manager.connect()
        await manager.close()

        mock_db_instance.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# S2 — Identity anchor CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_identity_anchor_returns_none_when_empty(mm):
    """get_identity_anchor returns None when no anchor has been saved."""
    result = await mm.get_identity_anchor("lyra")
    assert result is None


@pytest.mark.asyncio
async def test_save_then_get_identity_anchor(mm):
    """After save_identity_anchor, get_identity_anchor returns the saved value."""
    await mm.save_identity_anchor("lyra", "You are Lyra.")
    result = await mm.get_identity_anchor("lyra")
    assert result == "You are Lyra."


@pytest.mark.asyncio
async def test_save_identity_anchor_update(mm):
    """Saving identity anchor twice upserts (latest value wins)."""
    await mm.save_identity_anchor("lyra", "v1")
    await mm.save_identity_anchor("lyra", "v2")
    result = await mm.get_identity_anchor("lyra")
    assert result == "v2"


@pytest.mark.asyncio
async def test_identity_anchor_is_namespace_isolated(mm):
    """Identity anchors are isolated per namespace."""
    await mm.save_identity_anchor("lyra", "Lyra anchor")
    result = await mm.get_identity_anchor("other_agent")
    assert result is None


# ---------------------------------------------------------------------------
# S2 — Session upsert + contact upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_session_idempotent(mm, make_snap):
    """upsert_session with the same session_id is idempotent (no error)."""
    snap = make_snap()
    await mm.upsert_session(snap, "summary 1")
    await mm.upsert_session(snap, "summary 2")  # same session_id — upsert


@pytest.mark.asyncio
async def test_upsert_contact_no_error(mm, make_snap):
    """upsert_contact is idempotent — no error on repeated calls."""
    snap = make_snap()
    await mm.upsert_contact(snap.user_id, snap.medium, snap.agent_namespace)
    await mm.upsert_contact(snap.user_id, snap.medium, snap.agent_namespace)


# ---------------------------------------------------------------------------
# S6 — Cross-session recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_returns_empty_when_no_records(mm):
    """recall() returns empty string when no memory records exist."""
    result = await mm.recall("u1", "lyra", first_msg="", token_budget=1000)
    assert result == ""


@pytest.mark.asyncio
async def test_recall_returns_memory_block(mm, make_snap):
    """recall() returns a [MEMORY] block containing stored summaries."""
    snap = make_snap()
    await mm.upsert_session(snap, "discussed roxabi-vault")
    result = await mm.recall(
        snap.user_id,
        snap.agent_namespace,
        first_msg="",
        token_budget=1000,
    )
    assert "[MEMORY]" in result
    assert "roxabi-vault" in result


@pytest.mark.asyncio
async def test_recall_marks_stale_preferences(mm):
    """Preferences older than the staleness TTL are marked stale or excluded."""
    old_date = (datetime.now(UTC) - timedelta(days=45)).isoformat()
    # Insert a stale preference directly into the DB
    db = mm._db._db_or_raise()
    await db.execute(
        "INSERT INTO entries"
        " (type, content, namespace, metadata, created_at, updated_at)"
        " VALUES ('preference', 'prefers terse', 'lyra', ?, ?, ?)",
        (
            json.dumps(
                {
                    "user_id": "u1",
                    "name": "terse",
                    "domain": "communication",
                    "strength": 0.8,
                    "source_session_id": "s1",
                }
            ),
            old_date,
            old_date,
        ),
    )
    await db.commit()
    result = await mm.recall("u1", "lyra", first_msg="", token_budget=1000)
    # Either stale marker or empty (filtered) is acceptable
    assert result == "" or "[~" in result or "stale" in result.lower()


# ---------------------------------------------------------------------------
# S7 — Concept upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_concept_no_duplicate(mm, make_snap):
    """upsert_concept with the same concept name creates exactly one entry."""
    snap = make_snap()
    data = {
        "name": "roxabi-vault",
        "category": "technology",
        "content": "SQLite lib",
        "relations": [],
        "confidence": 0.9,
    }
    await mm.upsert_concept(snap, data)
    await mm.upsert_concept(snap, data)

    db = mm._db._db_or_raise()
    async with db.execute(
        "SELECT COUNT(*) FROM entries WHERE type='concept' AND namespace=?",
        (snap.agent_namespace,),
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == 1  # no duplicate inserted


@pytest.mark.asyncio
async def test_upsert_concept_increments_mention_count(mm, make_snap):
    """upsert_concept increments mention_count in metadata on each call."""
    snap = make_snap()
    data = {
        "name": "mylib",
        "category": "technology",
        "content": "a lib",
        "relations": [],
        "confidence": 0.9,
    }
    await mm.upsert_concept(snap, data)
    await mm.upsert_concept(snap, data)

    db = mm._db._db_or_raise()
    async with db.execute(
        "SELECT metadata FROM entries WHERE type='concept' AND namespace=?",
        (snap.agent_namespace,),
    ) as cur:
        row = await cur.fetchone()
    meta = json.loads(row[0])
    assert meta["mention_count"] == 2


@pytest.mark.asyncio
async def test_upsert_concept_provenance_set(mm, make_snap):
    """upsert_concept stores source_session_id in metadata."""
    snap = make_snap(session_id="sess-abc")
    data = {
        "name": "test-concept",
        "category": "fact",
        "content": "x",
        "relations": [],
        "confidence": 0.8,
    }
    await mm.upsert_concept(snap, data)

    db = mm._db._db_or_raise()
    async with db.execute(
        "SELECT metadata FROM entries WHERE type='concept' AND namespace=?",
        (snap.agent_namespace,),
    ) as cur:
        row = await cur.fetchone()
    meta = json.loads(row[0])
    assert meta["source_session_id"] == "sess-abc"


# ---------------------------------------------------------------------------
# S7 — Preference upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_preference_strength_increases(mm, make_snap):
    """upsert_preference strengthens (increases strength) on repeat calls."""
    snap = make_snap()
    data = {
        "name": "terse",
        "domain": "communication",
        "strength": 0.6,
        "source": "explicit",
    }
    await mm.upsert_preference(snap, data)
    await mm.upsert_preference(snap, data)

    db = mm._db._db_or_raise()
    async with db.execute(
        "SELECT metadata FROM entries WHERE type='preference' AND namespace=?",
        (snap.agent_namespace,),
    ) as cur:
        row = await cur.fetchone()
    meta = json.loads(row[0])
    assert meta["strength"] > 0.6  # strengthened beyond initial value


@pytest.mark.asyncio
async def test_upsert_preference_no_duplicate(mm, make_snap):
    """upsert_preference with same name/domain creates exactly one entry."""
    snap = make_snap()
    data = {
        "name": "verbose",
        "domain": "communication",
        "strength": 0.7,
        "source": "explicit",
    }
    await mm.upsert_preference(snap, data)
    await mm.upsert_preference(snap, data)

    db = mm._db._db_or_raise()
    async with db.execute(
        "SELECT COUNT(*) FROM entries WHERE type='preference' AND namespace=?",
        (snap.agent_namespace,),
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == 1
