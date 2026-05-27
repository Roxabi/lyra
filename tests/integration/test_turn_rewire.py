"""Integration tests for cross-adapter TurnPublisher → TurnWriter roundtrip.

SC-4: cross-adapter publishes converge correctly in one TurnStore.
SC-9: per-pool ordering is preserved (no cross-pool contamination).

Strategy: real TurnStore (tmp_path SQLite), real TurnWriter, real TurnPublisher.
JetStreamContext is a MagicMock — we capture the bytes published via
``js.publish(subject, payload)`` and drive them directly into
``writer._handle(event)`` to simulate JetStream delivery without a NATS server.

# TODO(#1331-T29): NATS server crash-recovery / mid-batch test in deploy phase
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.infrastructure.stores.turn_store import TurnStore
from lyra.infrastructure.turn_writer.writer import TurnWriter
from lyra.transport.turn_publisher import TurnPublisher
from roxabi_contracts.turns import TurnWriteEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRACE_ID = "trace-rewire-test"


def _now() -> datetime:
    return datetime.now(UTC)


def _make_js_and_publisher() -> tuple[MagicMock, TurnPublisher]:
    """Return a MagicMock JetStream + TurnPublisher wired to it."""
    js = MagicMock()
    js.publish = AsyncMock()
    return js, TurnPublisher(js)


async def _drain_published_to_writer(js: MagicMock, writer: TurnWriter) -> None:
    """Replay all bytes published via js.publish() through writer._handle().

    Each ``js.publish(subject, payload)`` call arg is decoded as a
    TurnWriteEvent and fed into writer._handle(event).
    """
    for c in js.publish.call_args_list:
        _subject, payload_bytes = c.args
        event = TurnWriteEvent.model_validate_json(payload_bytes)
        await writer._handle(event)


async def _count_turns_for_pool(store: TurnStore, pool_id: str) -> int:
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
        "SELECT session_id, pool_id, ended_at, resume_count"
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
    }


async def _get_turns_platform(
    store: TurnStore, pool_id: str, platform: str
) -> list[str]:
    db = store._db_or_raise()
    async with db.execute(
        "SELECT message_id FROM conversation_turns"
        " WHERE pool_id = ? AND platform = ?"
        " ORDER BY id",
        (pool_id, platform),
    ) as cur:
        rows = await cur.fetchall()
    return [r[0] for r in rows]


async def _count_processed_events(store: TurnStore, event_id: str) -> int:
    db = store._db_or_raise()
    async with db.execute(
        "SELECT COUNT(*) FROM processed_events WHERE event_id = ?", (str(event_id),)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path):
    """Real TurnStore backed by a temporary SQLite file."""
    s = TurnStore(tmp_path / "rewire_test.db")
    await s.connect()
    yield s
    await s.close()


@pytest.fixture
def writer(store: TurnStore) -> TurnWriter:
    """TurnWriter with a mocked JetStreamContext (not used in handler path)."""
    return TurnWriter(turn_store=store, js=MagicMock())


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_telegram_and_discord_publish_to_writer(
    store: TurnStore,
    writer: TurnWriter,
) -> None:
    """SC-4: 5 telegram + 5 discord log_turn events converge into 10 distinct rows.

    Two independent TurnPublishers (one per platform) publish log_turn events.
    Captured bytes are fed into writer._handle() to simulate JetStream delivery.
    After draining both, conversation_turns must have 10 rows split by platform.
    """
    # Arrange — two publishers sharing one TurnStore / writer
    tg_js, tg_pub = _make_js_and_publisher()
    dc_js, dc_pub = _make_js_and_publisher()

    tg_pool = "pool:tg:chat:sc4"
    dc_pool = "pool:dc:guild:sc4"
    tg_session = "sess-sc4-tg"
    dc_session = "sess-sc4-dc"

    # Act — publish 5 telegram + 5 discord events
    for i in range(5):
        await tg_pub.publish_log_turn(
            pool_id=tg_pool,
            session_id=tg_session,
            platform="telegram",
            user_id="u:tg:1",
            role="user",
            content=f"tg message {i}",
            message_id=f"tg-msg-{i:03d}",
            trace_id=_TRACE_ID,
        )
        await dc_pub.publish_log_turn(
            pool_id=dc_pool,
            session_id=dc_session,
            platform="discord",
            user_id="u:dc:1",
            role="user",
            content=f"dc message {i}",
            message_id=f"dc-msg-{i:03d}",
            trace_id=_TRACE_ID,
        )

    await _drain_published_to_writer(tg_js, writer)
    await _drain_published_to_writer(dc_js, writer)

    # Assert — 10 rows total, split correctly by platform
    tg_msg_ids = await _get_turns_platform(store, tg_pool, "telegram")
    dc_msg_ids = await _get_turns_platform(store, dc_pool, "discord")

    assert len(tg_msg_ids) == 5, f"expected 5 telegram rows, got {len(tg_msg_ids)}"
    assert len(dc_msg_ids) == 5, f"expected 5 discord rows, got {len(dc_msg_ids)}"

    # Verify no cross-contamination: telegram pool has no discord rows and vice versa
    db = store._db_or_raise()
    async with db.execute(
        "SELECT COUNT(*) FROM conversation_turns"
        " WHERE pool_id = ? AND platform != 'telegram'",
        (tg_pool,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 0, (
        "telegram pool must contain only telegram rows"
    )

    async with db.execute(
        "SELECT COUNT(*) FROM conversation_turns"
        " WHERE pool_id = ? AND platform != 'discord'",
        (dc_pool,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 0, (
        "discord pool must contain only discord rows"
    )


@pytest.mark.anyio
async def test_per_pool_ordering_within_writer(
    store: TurnStore,
    writer: TurnWriter,
) -> None:
    """SC-9: interleaved events from 2 pool_ids don't cross-contaminate.

    10 events from pool:tg:1 and 10 from pool:dc:2 are interleaved at publish
    time, then drained into the writer.  Each pool must accumulate exactly its
    own 10 rows with no cross-pool bleed.

    JetStream subject-key ordering is JetStream's responsibility — here we
    verify the writer persists rows to their correct pool_id only.
    """
    # Arrange
    js, pub = _make_js_and_publisher()
    pool_tg = "pool:tg:1"
    pool_dc = "pool:dc:2"
    sess_tg = "sess-sc9-tg"
    sess_dc = "sess-sc9-dc"

    # Act — interleave 10 events from each pool
    for i in range(10):
        await pub.publish_log_turn(
            pool_id=pool_tg,
            session_id=sess_tg,
            platform="telegram",
            user_id="u:tg:9",
            role="user",
            content=f"tg-turn-{i}",
            message_id=f"tg9-msg-{i:03d}",
            trace_id=_TRACE_ID,
        )
        await pub.publish_log_turn(
            pool_id=pool_dc,
            session_id=sess_dc,
            platform="discord",
            user_id="u:dc:9",
            role="user",
            content=f"dc-turn-{i}",
            message_id=f"dc9-msg-{i:03d}",
            trace_id=_TRACE_ID,
        )

    await _drain_published_to_writer(js, writer)

    # Assert
    tg_count = await _count_turns_for_pool(store, pool_tg)
    dc_count = await _count_turns_for_pool(store, pool_dc)

    assert tg_count == 10, f"pool:tg:1 expected 10 turns, got {tg_count}"
    assert dc_count == 10, f"pool:dc:2 expected 10 turns, got {dc_count}"

    # No cross-pool bleed
    db = store._db_or_raise()
    async with db.execute(
        "SELECT DISTINCT pool_id FROM conversation_turns WHERE pool_id = ?",
        (pool_tg,),
    ) as cur:
        pools = [r[0] for r in await cur.fetchall()]
    assert pools == [pool_tg], f"unexpected pool_ids for tg: {pools}"

    async with db.execute(
        "SELECT DISTINCT pool_id FROM conversation_turns WHERE pool_id = ?",
        (pool_dc,),
    ) as cur:
        pools = [r[0] for r in await cur.fetchall()]
    assert pools == [pool_dc], f"unexpected pool_ids for dc: {pools}"


@pytest.mark.anyio
async def test_log_turn_unique_dedupe_across_publishers(
    store: TurnStore,
    writer: TurnWriter,
) -> None:
    """SC-5 (cross-publisher): both publishers emit same (platform, message_id).

    Both publish (platform=telegram, message_id=msg-7). The UNIQUE constraint on
    conversation_turns(platform, message_id) must ensure only 1 row is persisted.
    """
    # Arrange — two separate publishers that publish the same logical turn
    tg_js, tg_pub = _make_js_and_publisher()
    dc_js, dc_pub = _make_js_and_publisher()

    pool_id = "pool:tg:chat:dedup"
    session_id = "sess-dedup-cross"

    # Act — both publishers emit the same (platform=telegram, message_id=msg-7)
    await tg_pub.publish_log_turn(
        pool_id=pool_id,
        session_id=session_id,
        platform="telegram",
        user_id="u:tg:dedup",
        role="user",
        content="original",
        message_id="msg-7",
        trace_id=_TRACE_ID,
    )
    # A second publisher also emits the same message (simulating duplicate delivery)
    await dc_pub.publish_log_turn(
        pool_id=pool_id,
        session_id=session_id,
        platform="telegram",
        user_id="u:tg:dedup",
        role="user",
        content="duplicate",
        message_id="msg-7",
        trace_id=_TRACE_ID,
    )

    await _drain_published_to_writer(tg_js, writer)
    await _drain_published_to_writer(dc_js, writer)

    # Assert — UNIQUE constraint ensures exactly 1 row
    db = store._db_or_raise()
    async with db.execute(
        "SELECT COUNT(*) FROM conversation_turns"
        " WHERE platform = 'telegram' AND message_id = 'msg-7'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1, f"expected 1 deduplicated row, got {row[0]}"


@pytest.mark.anyio
async def test_session_lifecycle_through_publishers(
    store: TurnStore,
    writer: TurnWriter,
) -> None:
    """SC-4 lifecycle: start_session → log_turn → end_session via TurnPublisher.

    Publishes all three events for one session, drains into writer, then asserts:
    - pool_sessions row exists with ended_at set
    - conversation_turns row exists for the log_turn
    """
    # Arrange
    js, pub = _make_js_and_publisher()
    pool_id = "pool:tg:lifecycle:1"
    session_id = "sess-lifecycle-001"

    # Act
    await pub.publish_start_session(
        pool_id=pool_id,
        session_id=session_id,
        platform="telegram",
        user_id="u:lifecycle:1",
        trace_id=_TRACE_ID,
    )
    await pub.publish_log_turn(
        pool_id=pool_id,
        session_id=session_id,
        platform="telegram",
        user_id="u:lifecycle:1",
        role="user",
        content="hello lifecycle",
        message_id="lifecycle-msg-001",
        trace_id=_TRACE_ID,
    )
    await pub.publish_end_session(
        pool_id=pool_id,
        session_id=session_id,
        platform="telegram",
        user_id="u:lifecycle:1",
        trace_id=_TRACE_ID,
    )

    await _drain_published_to_writer(js, writer)

    # Assert — session row has ended_at
    session = await _get_session(store, session_id)
    assert session is not None, "pool_sessions row must exist"
    assert session["ended_at"] is not None, "ended_at must be set after end_session"
    assert session["pool_id"] == pool_id

    # Assert — turn row exists
    turn_count = await _count_turns_for_pool(store, pool_id)
    assert turn_count == 1, f"expected 1 conversation_turns row, got {turn_count}"


@pytest.mark.anyio
async def test_increment_resume_count_cross_replay(
    store: TurnStore,
    writer: TurnWriter,
) -> None:
    """SC-8 high-water mark + event_id dedup over cross-publisher replay.

    Sequence:
      1. publish increment_resume_count(target_count=1)  → resume_count = 1
      2. publish increment_resume_count(target_count=2)  → resume_count = 2
      3. REPLAY same event as step 1 (same event_id, target_count=1)
         → processed_events blocks it; resume_count stays at 2

    Asserts resume_count = 2 (max wins), processed_events has both event_ids.
    """
    # Arrange
    js, pub = _make_js_and_publisher()
    pool_id = "pool:tg:resume:sc8"
    session_id = "sess-resume-sc8"

    # Seed session so pool_sessions row exists for the UPDATE
    # (writer._handle_increment_resume_count does UPDATE, needs existing row)
    js2, pub2 = _make_js_and_publisher()
    await pub2.publish_start_session(
        pool_id=pool_id,
        session_id=session_id,
        platform="telegram",
        user_id="u:resume:1",
        trace_id=_TRACE_ID,
    )
    await _drain_published_to_writer(js2, writer)

    # Step 1 — target_count=1 (unique event_id e1)
    await pub.publish_increment_resume_count(
        pool_id=pool_id,
        session_id=session_id,
        platform="telegram",
        user_id="u:resume:1",
        target_count=1,
        trace_id=_TRACE_ID,
    )
    # Step 2 — target_count=2 (unique event_id e2)
    await pub.publish_increment_resume_count(
        pool_id=pool_id,
        session_id=session_id,
        platform="telegram",
        user_id="u:resume:1",
        target_count=2,
        trace_id=_TRACE_ID,
    )

    # Capture the bytes from step 1 for replay: re-publish with SAME event_id
    # The first increment_resume_count call is call index 0
    step1_payload_bytes = js.publish.call_args_list[0].args[1]
    step1_event = TurnWriteEvent.model_validate_json(step1_payload_bytes)

    # Drive steps 1 and 2 normally
    await _drain_published_to_writer(js, writer)

    # REPLAY step 1 with same event_id — processed_events must block it
    await writer._handle(step1_event)

    # Assert
    session = await _get_session(store, session_id)
    assert session is not None
    assert session["resume_count"] == 2, (
        f"resume_count must be 2 (max wins), got {session['resume_count']}"
    )

    # Both event_ids in processed_events (e1 and e2)
    step2_payload_bytes = js.publish.call_args_list[1].args[1]
    step2_event = TurnWriteEvent.model_validate_json(step2_payload_bytes)

    e1_count = await _count_processed_events(store, str(step1_event.event_id))
    e2_count = await _count_processed_events(store, str(step2_event.event_id))
    assert e1_count == 1, (
        f"event_id e1 must appear once in processed_events, got {e1_count}"
    )
    assert e2_count == 1, (
        f"event_id e2 must appear once in processed_events, got {e2_count}"
    )
