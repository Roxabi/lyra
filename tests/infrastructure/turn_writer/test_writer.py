"""Tests for TurnWriter._handle* dispatch chain (issue #1331 — T13).

Strategy: real TurnStore (in-memory SQLite), mocked JetStreamContext for __init__
only. Drive via _handle(event) directly — no real NATS loop.

Cases:
  1. test_log_turn_writes_row
  2. test_log_turn_dedupe_on_unique        (SC-5)
  3. test_start_session_idempotent         (SC-6)
  4. test_end_session_idempotent           (SC-6)
  5. test_set_cli_session_overwrite        (SC-7)
  6. test_increment_resume_count_high_water_mark  (SC-8)
  7. test_increment_resume_count_event_id_dedup   (SC-8)
  8. test_per_pool_order_preserved         (SC-9 isolation only — JetStream ordering
                                            not tested here)

# TODO(#1331-T29): real crash-recovery / mid-batch test in deploy phase
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import nats.errors
import pytest

from factory.infrastructure.stores.turn_store import TurnStore
from factory.infrastructure.turn_writer.writer import TurnWriter
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.turns import (
    EndSessionPayload,
    IncrementResumeCountPayload,
    LogTurnPayload,
    SetCliSessionPayload,
    StartSessionPayload,
    TurnWriteEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PLATFORM = "telegram"
_USER_ID = "u:test:1"
_TRACE_ID = "trace-test-001"


def _now() -> datetime:
    return datetime.now(UTC)


def _event(  # noqa: PLR0913
    payload,
    *,
    pool_id: str = "pool:tg:chat:1",
    session_id: str = "sess-test-0001",
    platform: str = _PLATFORM,
    user_id: str = _USER_ID,
    event_id=None,
) -> TurnWriteEvent:
    """Build a TurnWriteEvent with the given payload and envelope fields."""
    return TurnWriteEvent(
        contract_version=CONTRACT_VERSION,
        trace_id=_TRACE_ID,
        issued_at=_now(),
        event_id=event_id or uuid4(),
        pool_id=pool_id,
        session_id=session_id,
        platform=platform,
        user_id=user_id,
        timestamp=_now(),
        payload=payload,
    )


def _log_payload(
    role: Literal["user", "assistant"] = "user",
    content: str = "hello",
    message_id: str = "msg-001",
) -> LogTurnPayload:
    return LogTurnPayload(role=role, content=content, message_id=message_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path):
    """TurnStore backed by a temporary SQLite file."""
    s = TurnStore(tmp_path / "test.db")
    await s.connect()
    yield s
    await s.close()


@pytest.fixture
def writer(store: TurnStore) -> TurnWriter:
    """TurnWriter with a mocked JetStreamContext (not used in handler tests)."""
    return TurnWriter(turn_store=store, js=MagicMock())


# ---------------------------------------------------------------------------
# Helpers for reading state
# ---------------------------------------------------------------------------


async def _count_turns(store: TurnStore, pool_id: str) -> int:
    db = store._db_or_raise()
    async with db.execute(
        "SELECT COUNT(*) FROM conversation_turns WHERE pool_id = ?", (pool_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


async def _get_session(store: TurnStore, session_id: str) -> dict | None:
    db = store._db_or_raise()
    async with db.execute(
        "SELECT session_id, pool_id, ended_at, resume_count, cli_session_id"
        " FROM pool_sessions WHERE session_id = ?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "session_id": row[0],
        "pool_id": row[1],
        "ended_at": row[2],
        "resume_count": row[3],
        "cli_session_id": row[4],
    }


async def _count_processed_events(store: TurnStore, event_id: str) -> int:
    db = store._db_or_raise()
    async with db.execute(
        "SELECT COUNT(*) FROM processed_events WHERE event_id = ?", (str(event_id),)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_turn_writes_row(writer: TurnWriter, store: TurnStore) -> None:
    """SC-1: a single log_turn event persists one row with correct fields."""
    pool_id = "pool:tg:chat:10"
    session_id = "sess-log-001"
    ev = _event(
        _log_payload(role="user", content="hello world", message_id="msg-a1"),
        pool_id=pool_id,
        session_id=session_id,
    )
    await writer._handle(ev)

    db = store._db_or_raise()
    async with db.execute(
        "SELECT pool_id, session_id, role, content, message_id"
        " FROM conversation_turns WHERE pool_id = ?",
        (pool_id,),
    ) as cur:
        row = await cur.fetchone()

    assert row is not None
    assert row[0] == pool_id
    assert row[1] == session_id
    assert row[2] == "user"
    assert row[3] == "hello world"
    assert row[4] == "msg-a1"


@pytest.mark.anyio
async def test_log_turn_dedupe_on_unique(writer: TurnWriter, store: TurnStore) -> None:
    """SC-5: duplicate (platform, message_id) is a no-op — only 1 row persisted."""
    pool_id = "pool:tg:chat:11"
    session_id = "sess-dedup-001"
    payload = _log_payload(message_id="msg-dedup-1")
    ev1 = _event(payload, pool_id=pool_id, session_id=session_id)
    ev2 = _event(payload, pool_id=pool_id, session_id=session_id)

    await writer._handle(ev1)
    await writer._handle(ev2)

    count = await _count_turns(store, pool_id)
    assert count == 1


@pytest.mark.anyio
async def test_start_session_idempotent(writer: TurnWriter, store: TurnStore) -> None:
    """SC-6: start_session uses INSERT OR IGNORE — duplicate is silently dropped."""
    session_id = "sess-start-001"
    pool_id = "pool:tg:chat:20"
    payload = StartSessionPayload()
    ev1 = _event(payload, pool_id=pool_id, session_id=session_id)
    ev2 = _event(payload, pool_id=pool_id, session_id=session_id)

    await writer._handle(ev1)
    await writer._handle(ev2)

    db = store._db_or_raise()
    async with db.execute(
        "SELECT COUNT(*) FROM pool_sessions WHERE session_id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.anyio
async def test_end_session_idempotent(writer: TurnWriter, store: TurnStore) -> None:
    """SC-6: end_session stamps ended_at once; second call is no-op."""
    session_id = "sess-end-001"
    pool_id = "pool:tg:chat:21"

    # Set up session first
    await writer._handle(
        _event(StartSessionPayload(), pool_id=pool_id, session_id=session_id)
    )

    end_ev = _event(EndSessionPayload(), pool_id=pool_id, session_id=session_id)
    await writer._handle(end_ev)
    session_after_first = await _get_session(store, session_id)
    assert session_after_first is not None
    first_ended_at = session_after_first["ended_at"]
    assert first_ended_at is not None

    # Second end — no-op (WHERE ended_at IS NULL skips)
    await writer._handle(end_ev)
    session_after_second = await _get_session(store, session_id)
    assert session_after_second is not None
    assert session_after_second["ended_at"] == first_ended_at


@pytest.mark.anyio
async def test_set_cli_session_overwrite(writer: TurnWriter, store: TurnStore) -> None:
    """SC-7: set_cli_session is an UPDATE — second call with new value wins."""
    session_id = "sess-cli-001"
    pool_id = "pool:tg:chat:30"

    await writer._handle(
        _event(StartSessionPayload(), pool_id=pool_id, session_id=session_id)
    )

    ev1 = _event(
        SetCliSessionPayload(cli_session_id="cli-aaa"),
        pool_id=pool_id,
        session_id=session_id,
    )
    ev2 = _event(
        SetCliSessionPayload(cli_session_id="cli-bbb"),
        pool_id=pool_id,
        session_id=session_id,
    )

    await writer._handle(ev1)
    session_v1 = await _get_session(store, session_id)
    assert session_v1 is not None
    assert session_v1["cli_session_id"] == "cli-aaa"

    await writer._handle(ev2)
    session_v2 = await _get_session(store, session_id)
    assert session_v2 is not None
    assert session_v2["cli_session_id"] == "cli-bbb"


@pytest.mark.anyio
async def test_increment_resume_count_high_water_mark(
    writer: TurnWriter, store: TurnStore
) -> None:
    """SC-8: high-water mark — replay of older target_count does not regress."""
    session_id = "sess-hwm-001"
    pool_id = "pool:tg:chat:40"

    await writer._handle(
        _event(StartSessionPayload(), pool_id=pool_id, session_id=session_id)
    )

    # target_count=1: resume_count should become 1
    ev1 = _event(
        IncrementResumeCountPayload(target_count=1),
        pool_id=pool_id,
        session_id=session_id,
    )
    await writer._handle(ev1)

    # target_count=2: resume_count should become 2
    ev2 = _event(
        IncrementResumeCountPayload(target_count=2),
        pool_id=pool_id,
        session_id=session_id,
    )
    await writer._handle(ev2)

    # REPLAY: target_count=1 (older event redelivered) — must NOT regress
    ev1_replay = _event(
        IncrementResumeCountPayload(target_count=1),
        pool_id=pool_id,
        session_id=session_id,
    )
    await writer._handle(ev1_replay)

    session = await _get_session(store, session_id)
    assert session is not None
    assert session["resume_count"] == 2


@pytest.mark.anyio
async def test_increment_resume_count_event_id_dedup(
    writer: TurnWriter, store: TurnStore
) -> None:
    """SC-8: processed_events table blocks double-processing of same event_id."""
    session_id = "sess-evtdedup-001"
    pool_id = "pool:tg:chat:41"
    fixed_event_id = uuid4()

    await writer._handle(
        _event(StartSessionPayload(), pool_id=pool_id, session_id=session_id)
    )

    ev = _event(
        IncrementResumeCountPayload(target_count=1),
        pool_id=pool_id,
        session_id=session_id,
        event_id=fixed_event_id,
    )

    # First delivery — processed normally
    await writer._handle(ev)

    # Second delivery — same event_id; processed_events table should block it
    await writer._handle(ev)

    # processed_events must have exactly 1 row for this event_id
    count = await _count_processed_events(store, str(fixed_event_id))
    assert count == 1

    # resume_count must be 1 (not 2)
    session = await _get_session(store, session_id)
    assert session is not None
    assert session["resume_count"] == 1


@pytest.mark.anyio
async def test_per_pool_order_preserved(writer: TurnWriter, store: TurnStore) -> None:
    """SC-9: two different pool_ids accumulate turns independently.

    Tests isolation only — JetStream subject-key ordering is not tested here.
    # TODO(#1331-T29): real crash-recovery / mid-batch test in deploy phase
    """
    pool_a = "pool:tg:chat:100"
    pool_b = "pool:tg:chat:101"
    session_a = "sess-iso-a001"
    session_b = "sess-iso-b001"

    events = [
        _event(
            _log_payload(content="a-1", message_id="msg-a-001"),
            pool_id=pool_a,
            session_id=session_a,
        ),
        _event(
            _log_payload(content="b-1", message_id="msg-b-001"),
            pool_id=pool_b,
            session_id=session_b,
        ),
        _event(
            _log_payload(content="a-2", message_id="msg-a-002"),
            pool_id=pool_a,
            session_id=session_a,
        ),
        _event(
            _log_payload(content="b-2", message_id="msg-b-002"),
            pool_id=pool_b,
            session_id=session_b,
        ),
        _event(
            _log_payload(content="a-3", message_id="msg-a-003"),
            pool_id=pool_a,
            session_id=session_a,
        ),
        _event(
            _log_payload(content="b-3", message_id="msg-b-003"),
            pool_id=pool_b,
            session_id=session_b,
        ),
    ]

    for ev in events:
        await writer._handle(ev)

    count_a = await _count_turns(store, pool_a)
    count_b = await _count_turns(store, pool_b)
    assert count_a == 3, f"pool_a expected 3 turns, got {count_a}"
    assert count_b == 3, f"pool_b expected 3 turns, got {count_b}"

    # Verify no cross-pool contamination: turns for pool_a have pool_a pool_id only
    db = store._db_or_raise()
    async with db.execute(
        "SELECT DISTINCT pool_id FROM conversation_turns WHERE pool_id = ?",
        (pool_a,),
    ) as cur:
        rows_a = list(await cur.fetchall())
    assert len(rows_a) == 1 and rows_a[0][0] == pool_a

    async with db.execute(
        "SELECT DISTINCT pool_id FROM conversation_turns WHERE pool_id = ?",
        (pool_b,),
    ) as cur:
        rows_b = list(await cur.fetchall())
    assert len(rows_b) == 1 and rows_b[0][0] == pool_b


@pytest.mark.anyio
async def test_consume_loop_propagates_connection_closed_error(
    store: TurnStore,
) -> None:
    """ConnectionClosedError in fetch must re-raise (not fall through to nak path).

    Negative test: if the except nats.errors.ConnectionClosedError clause at
    writer.py:122-127 is deleted, the error is swallowed by the inner
    except Exception block which naks and continues — exactly the wrong
    behaviour. This test fails in that scenario.
    """
    # Arrange: writer whose _sub.fetch raises ConnectionClosedError immediately.
    mock_sub = MagicMock()
    mock_sub.fetch = AsyncMock(
        side_effect=nats.errors.ConnectionClosedError("nats: connection closed")
    )
    mock_js = MagicMock()
    w = TurnWriter(turn_store=store, js=mock_js)
    w._sub = mock_sub

    # Act + Assert: the loop propagates rather than swallowing the error.
    with patch("factory.infrastructure.turn_writer.writer.log") as mock_log:
        with pytest.raises(nats.errors.ConnectionClosedError):
            await w._consume_loop()

        # Also verify log.error was called with the expected message fragment.
        assert mock_log.error.called
        call_args = mock_log.error.call_args
        assert "NATS connection lost" in call_args[0][0]
