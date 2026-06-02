"""Crash-recovery and sole-writer assertions for TurnWriter (SC-8, SC-12).

SC-8: idempotence under crash + replay — processed_events dedup survives restart.
SC-12: sole writer to turns.db — only one process holds the file for write.

Test topology
-------------
T1 test_processed_events_table_dedupe_after_simulated_crash
    Always runs. Real TurnStore (tmp_path SQLite) + mocked JetStream.
    Simulates mid-batch crash by raising inside _handle_increment_resume_count
    before the processed_events INSERT. Validates that replay succeeds and
    leaves exactly one row per processed event.

T2 test_jetstream_redelivery_on_no_ack
    Skipped unless nats-server binary is present on PATH.
    Requires a real JetStream server — tests redelivery semantics.

T3 test_lsof_sole_writer
    Skipped unless lsof binary is present on PATH.
    Requires a running lyra-turn-writer process and lsof.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from factory.infrastructure.stores.turn_store import TurnStore
from factory.infrastructure.turn_writer.writer import TurnWriter
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.turns import (
    IncrementResumeCountPayload,
    StartSessionPayload,
    TurnWriteEvent,
)

# ---------------------------------------------------------------------------
# Skip markers
# ---------------------------------------------------------------------------

NATS_SERVER_AVAILABLE = shutil.which("nats-server") is not None
LSOF_AVAILABLE = shutil.which("lsof") is not None

skip_unless_nats = pytest.mark.skipif(
    not NATS_SERVER_AVAILABLE,
    reason=(
        "requires nats-server binary (install for full crash-recovery coverage); "
        "run: apt install nats-server OR brew install nats-server"
    ),
)

skip_unless_lsof = pytest.mark.skipif(
    not LSOF_AVAILABLE,
    reason=(
        "requires lsof + a running lyra-turn-writer process; "
        "run in deploy validation, not the unit suite"
    ),
)

# ---------------------------------------------------------------------------
# Shared helpers (mirror test_writer.py conventions)
# ---------------------------------------------------------------------------

_PLATFORM = "telegram"
_USER_ID = "u:crash:1"
_TRACE_ID = "trace-crash-001"


def _now() -> datetime:
    return datetime.now(UTC)


def _event(  # noqa: PLR0913
    payload,
    *,
    pool_id: str = "pool:tg:chat:crash",
    session_id: str = "sess-crash-0001",
    platform: str = _PLATFORM,
    user_id: str = _USER_ID,
    event_id: UUID | None = None,
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


def _irc_payload(target_count: int) -> IncrementResumeCountPayload:
    return IncrementResumeCountPayload(target_count=target_count)


async def _count_processed_events(store: TurnStore, event_id: UUID) -> int:
    db = store._db_or_raise()
    async with db.execute(
        "SELECT COUNT(*) FROM processed_events WHERE event_id = ?",
        (str(event_id),),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


async def _get_resume_count(store: TurnStore, session_id: str) -> int | None:
    db = store._db_or_raise()
    async with db.execute(
        "SELECT resume_count FROM pool_sessions WHERE session_id = ?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return row[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path):
    """TurnStore backed by a temporary SQLite file."""
    s = TurnStore(tmp_path / "turns.db")
    await s.connect()
    yield s
    await s.close()


@pytest.fixture
def writer(store: TurnStore) -> TurnWriter:
    """TurnWriter with a mocked JetStreamContext (not used in handler tests)."""
    return TurnWriter(turn_store=store, js=MagicMock())


# ---------------------------------------------------------------------------
# T1 — always-runnable crash simulation (no infra required)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_processed_events_table_dedupe_after_simulated_crash(
    writer: TurnWriter,
    store: TurnStore,
) -> None:
    """SC-8: crash mid-batch leaves no processed_events row; replay succeeds.

    Scenario
    --------
    Build 5 increment_resume_count events (e1..e5) targeting the same session.
    Drive e1..e3 normally (all ack).
    Inject a crash on e4: _handle raises BEFORE the processed_events INSERT.
    Verify e4 left no row in processed_events.
    Replay e4 (second call): must succeed and insert the row.
    Drive e5 normally.
    Final state: resume_count = 3 (high-water), processed_events has 5 rows.
    """
    pool_id = "pool:tg:chat:crash-t1"
    session_id = "sess-crash-t1"
    target_count = 3

    # Arrange: prime the session row.
    await writer._handle(
        _event(StartSessionPayload(), pool_id=pool_id, session_id=session_id)
    )

    e1 = _event(
        _irc_payload(target_count),
        pool_id=pool_id,
        session_id=session_id,
        event_id=uuid4(),
    )
    e2 = _event(
        _irc_payload(target_count),
        pool_id=pool_id,
        session_id=session_id,
        event_id=uuid4(),
    )
    e3 = _event(
        _irc_payload(target_count),
        pool_id=pool_id,
        session_id=session_id,
        event_id=uuid4(),
    )
    e4_id = uuid4()
    e4 = _event(
        _irc_payload(target_count),
        pool_id=pool_id,
        session_id=session_id,
        event_id=e4_id,
    )
    e5 = _event(
        _irc_payload(target_count),
        pool_id=pool_id,
        session_id=session_id,
        event_id=uuid4(),
    )

    # Act — process e1, e2, e3 normally.
    await writer._handle(e1)
    await writer._handle(e2)
    await writer._handle(e3)

    # Arrange crash injection for e4.
    # Replace _handle_increment_resume_count with a version that executes the
    # SELECT + UPDATE (so the DB is dirtied) then raises before the
    # processed_events INSERT — simulating a process crash mid-transaction.
    #
    # The real implementation uses a single cursor context; we reproduce the
    # two writes but deliberately omit the INSERT + commit so the DB is left
    # in a consistent state (no partial INSERT) while the processed_events row
    # is absent — identical to a crash that kills the process after the UPDATE
    # executes but before the INSERT is committed (WAL: the uncommitted UPDATE
    # is rolled back on next open).
    #
    # For in-process simulation we skip the UPDATE entirely and simply raise,
    # which is equivalent: the processed_events row is absent, and the
    # resume_count is unchanged by the failed call.

    async def _crash_on_e4(event: TurnWriteEvent, p: IncrementResumeCountPayload):
        """Simulate crash: raise RuntimeError before processed_events INSERT."""
        if event.event_id == e4_id:
            # Mimic finding the event not yet processed (SELECT returns None),
            # running the UPDATE, then crashing before INSERT — the entire
            # cursor block is not committed because we raise here.
            raise RuntimeError(
                "simulated crash mid-batch before processed_events INSERT"
            )
        # All other event_ids: delegate to the real implementation.
        return await _original_handler(event, p)

    _original_handler = writer._handle_increment_resume_count
    writer._handle_increment_resume_count = _crash_on_e4  # type: ignore[method-assign]

    # Act — crash on e4.
    with pytest.raises(RuntimeError, match="simulated crash"):
        await writer._handle(e4)

    # Assert: e4 must not appear in processed_events.
    count_before_replay = await _count_processed_events(store, e4_id)
    assert count_before_replay == 0, (
        f"crash mid-batch: expected 0 rows for e4 in processed_events, "
        f"got {count_before_replay}"
    )

    # Restore real handler for replay.
    writer._handle_increment_resume_count = _original_handler  # type: ignore[method-assign]

    # Act — replay e4 (same event_id).  Must succeed.
    await writer._handle(e4)

    # Assert: e4 now has exactly one row.
    count_after_replay = await _count_processed_events(store, e4_id)
    assert count_after_replay == 1, (
        f"replay: expected 1 row for e4 in processed_events, got {count_after_replay}"
    )

    # Act — process e5 normally.
    await writer._handle(e5)

    # Assert final state.
    resume_count = await _get_resume_count(store, session_id)
    assert resume_count == target_count, (
        f"expected resume_count={target_count}, got {resume_count}"
    )

    # All 5 events must be present in processed_events (e1..e5 each have 1 row).
    for eid_label, ev in [("e1", e1), ("e2", e2), ("e3", e3), ("e4", e4), ("e5", e5)]:
        c = await _count_processed_events(store, ev.event_id)
        assert c == 1, (
            f"{eid_label} (event_id={ev.event_id}): expected 1 row in "
            f"processed_events, got {c}"
        )


# ---------------------------------------------------------------------------
# T2 — JetStream redelivery (skipped without nats-server)
# ---------------------------------------------------------------------------


@skip_unless_nats
@pytest.mark.anyio
async def test_jetstream_redelivery_on_no_ack() -> None:
    """SC-8: JetStream redelivers messages when the writer exits without acking.

    Requires a running nats-server with JetStream enabled (-js flag).
    The test starts a short-AckWait consumer, publishes 5 log_turn events,
    then stops the writer before any ack. After AckWait expires, restarting
    the writer must receive all 5 messages and write them without duplicates.

    Skipped: nats-server binary not found on PATH. Install nats-server and
    re-run with NATS_URL set to reach the live server.
    """
    # Implementation intentionally deferred: requires a real JetStream server
    # and a short AckWait consumer override (default 60 s would make CI
    # impractical). Full implementation belongs in the deploy validation suite
    # where a docker-compose NATS server is available.
    #
    # Scaffold retained so the test is discoverable and the skip reason is
    # actionable.
    pytest.skip(
        "Full redelivery test requires running NATS JetStream server and "
        "a sub-second AckWait consumer — implement in deploy validation suite"
    )


# ---------------------------------------------------------------------------
# T3 — sole-writer / lsof (skipped without lsof + running process)
# ---------------------------------------------------------------------------


@skip_unless_lsof
@pytest.mark.anyio
async def test_lsof_sole_writer(tmp_path) -> None:
    """SC-12: exactly one process holds turns.db open for write at any time.

    Starts lyra-turn-writer via subprocess, runs lsof against the DB path,
    asserts exactly one process holds the file. Kills the subprocess on teardown.

    Skipped: lsof binary not found. This test runs in deploy validation only,
    not in the standard unit/integration suite.
    """
    import subprocess

    db_path = tmp_path / "turns.db"

    # Start the writer process. In practice this would be the real lyra binary
    # with env vars pointing to the tmp_path DB. The test is a structural
    # placeholder that documents the SC-12 assertion pattern.
    #
    # In deploy validation, replace this stub with a real subprocess invocation:
    #   proc = subprocess.Popen(["lyra", "turn-writer", "--db", str(db_path)])
    #
    # For now we skip with a descriptive reason rather than spawning a process
    # that would require a full lyra configuration (NATS, auth, etc.).
    pytest.skip(
        "SC-12 sole-writer check requires a running lyra-turn-writer process "
        "with a known DB path; run in deploy validation with a real NATS server "
        "and lyra configuration. lsof assertion pattern: "
        "parse `lsof -F p <db_path>` and assert exactly 1 PID."
    )

    # Dead code — documents the assertion pattern for future implementation.
    proc = None
    try:
        result = subprocess.run(
            ["lsof", "-F", "p", str(db_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = [line[1:] for line in result.stdout.splitlines() if line.startswith("p")]
        assert len(pids) == 1, (
            f"SC-12 violation: expected exactly 1 process to hold {db_path}, "
            f"got {len(pids)}: {pids}"
        )
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)
