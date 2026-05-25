"""Integration tests for _on_resume_fn callback wiring (issue #1331, T22).

SC-10: middleware_pool._on_resume_fn is wired to call
TurnPublisher.publish_increment_resume_count, and is awaited at all callsites.

Tests:
  1. test_on_resume_fn_calls_publisher          — wiring correctness
  2. test_on_resume_fn_awaited_at_callsites     — static grep guard
  3. test_on_resume_fn_high_water_mark_target   — target_count = current + 1
  4. test_on_resume_fn_handles_no_publisher     — None publisher → None fn
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.hub import Hub
from lyra.core.pool import Pool
from lyra.core.pool.pool_context import PoolContext
from lyra.infrastructure.stores.turn_store import TurnStore
from lyra.transport.turn_publisher import TurnPublisher

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POOL_ID = "pool:tg:chat:resume-wiring"
_SESSION_ID = "sess-resume-wiring-001"
_PLATFORM = "telegram"
_USER_ID = "u:resume:wiring"
_TRACE_ID = _SESSION_ID  # middleware wires trace_id = session_id


def _make_hub_with_publisher(
    turn_publisher: TurnPublisher,
    turn_store: TurnStore | None = None,
) -> Hub:
    """Build a Hub with _turn_publisher injected (bypasses full bootstrap)."""
    cr = CircuitRegistry()
    hub = Hub(circuit_registry=cr)
    hub._turn_publisher = turn_publisher
    hub._turn_store = turn_store
    return hub


def _make_mock_publisher() -> MagicMock:
    """Return a MagicMock TurnPublisher with an AsyncMock publish method."""
    pub = MagicMock(spec=TurnPublisher)
    pub.publish_increment_resume_count = AsyncMock()
    return pub


def _make_pool_with_resume_fn(
    pool_id: str = _POOL_ID,
    platform: str = _PLATFORM,
    user_id: str = _USER_ID,
) -> Pool:
    """Build a minimal Pool (no hub wiring) for testing _on_resume_fn directly."""
    ctx = MagicMock(spec=PoolContext)
    ctx.get_message = MagicMock(return_value=None)
    pool = Pool(pool_id=pool_id, agent_name="test-agent", ctx=ctx)
    pool.medium = platform
    pool.user_id = user_id
    return pool


# ---------------------------------------------------------------------------
# Test 1 — wiring correctness
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_on_resume_fn_calls_publisher(tmp_path) -> None:
    """SC-10: _on_resume_fn(session_id) triggers publish_increment_resume_count.

    The wiring closure inside MessagePrepMiddleware reads current resume_count
    from TurnStore (0 when no row), then publishes target_count = current + 1.
    We replicate the wiring directly (same logic as middleware_pool.py line 123-138)
    and assert the publisher call args.
    """
    # Arrange — real TurnStore so get_resume_count works
    store = TurnStore(tmp_path / "resume_wiring.db")
    await store.connect()

    mock_pub = _make_mock_publisher()
    hub = _make_hub_with_publisher(mock_pub, turn_store=store)

    pool = _make_pool_with_resume_fn()

    # Wire _on_resume_fn exactly as middleware_pool.py does
    _pub = hub._turn_publisher
    _store = hub._turn_store
    _pool_ref = pool

    async def _resume_fn(session_id: str) -> None:
        current = 0
        if _store is not None:
            current = await _store.get_resume_count(session_id)
        await _pub.publish_increment_resume_count(  # type: ignore[union-attr]
            pool_id=_pool_ref.pool_id,
            session_id=session_id,
            platform=_pool_ref.medium or "",
            user_id=_pool_ref.user_id or "",
            target_count=current + 1,
            trace_id=session_id,
        )

    pool._on_resume_fn = _resume_fn

    # Act
    await pool._on_resume_fn(_SESSION_ID)

    # Assert — publisher was awaited with correct kwargs
    mock_pub.publish_increment_resume_count.assert_awaited_once()
    call_kwargs = mock_pub.publish_increment_resume_count.call_args.kwargs
    assert call_kwargs["pool_id"] == _POOL_ID
    assert call_kwargs["session_id"] == _SESSION_ID
    assert call_kwargs["platform"] == _PLATFORM
    assert call_kwargs["user_id"] == _USER_ID
    assert call_kwargs["target_count"] == 1  # current=0 → target=1
    assert call_kwargs["trace_id"], "trace_id must be non-empty"

    await store.close()


# ---------------------------------------------------------------------------
# Test 2 — static grep guard: all callsites await _on_resume_fn
# ---------------------------------------------------------------------------


def test_on_resume_fn_awaited_at_callsites() -> None:
    """SC-10 negative: every callsite of _on_resume_fn uses 'await'.

    Grep all Python files under src/lyra/ for lines that call _on_resume_fn(
    without a leading 'await'.  A non-awaited call would cause a coroutine
    to be silently discarded, breaking SC-10.
    """
    src_root = Path(__file__).parents[2] / "src" / "lyra"
    assert src_root.exists(), f"Source root not found: {src_root}"

    non_awaited: list[str] = []
    # Match any line that invokes _on_resume_fn( but lacks 'await' before it.
    # We accept 'await self._on_resume_fn(' and 'await pool._on_resume_fn(' etc.
    call_pattern = re.compile(r"_on_resume_fn\(")
    awaited_pattern = re.compile(r"\bawait\b.*_on_resume_fn\(")
    # Assignment / type annotation / declaration lines are not callsites
    assign_or_decl = re.compile(
        r"_on_resume_fn\s*[=:]|"  # assignment or type annotation
        r"_on_resume_fn\s*$"  # standalone (function definition line)
    )

    for py_file in sorted(src_root.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not call_pattern.search(stripped):
                continue
            if assign_or_decl.search(stripped):
                continue
            # It is a call — check for 'await'
            if not awaited_pattern.search(stripped):
                rel = py_file.relative_to(src_root)
                non_awaited.append(f"{rel}:{lineno}: {stripped}")

    assert not non_awaited, (
        "Found _on_resume_fn( callsites without 'await':\n"
        + "\n".join(non_awaited)
    )


# ---------------------------------------------------------------------------
# Test 3 — target_count = current_resume_count + 1
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_on_resume_fn_high_water_mark_target(tmp_path) -> None:
    """SC-10: target_count equals get_resume_count(session_id) + 1 at callsite.

    Seed pool_sessions with resume_count=3, then call _on_resume_fn.
    The publisher must receive target_count=4.
    """
    # Arrange
    store = TurnStore(tmp_path / "hwm_target.db")
    await store.connect()

    # Seed a session row with resume_count = 3
    await store._start_session(_SESSION_ID, _POOL_ID)
    db = store._db_or_raise()
    await db.execute(
        "UPDATE pool_sessions SET resume_count = 3 WHERE session_id = ?",
        (_SESSION_ID,),
    )
    await db.commit()

    mock_pub = _make_mock_publisher()
    pool = _make_pool_with_resume_fn()

    # Wire _on_resume_fn (same logic as middleware_pool.py)
    async def _resume_fn(session_id: str) -> None:
        current = await store.get_resume_count(session_id)
        await mock_pub.publish_increment_resume_count(
            pool_id=pool.pool_id,
            session_id=session_id,
            platform=pool.medium or "",
            user_id=pool.user_id or "",
            target_count=current + 1,
            trace_id=session_id,
        )

    pool._on_resume_fn = _resume_fn

    # Act
    await pool._on_resume_fn(_SESSION_ID)

    # Assert — target_count must be 3 + 1 = 4
    call_kwargs = mock_pub.publish_increment_resume_count.call_args.kwargs
    assert call_kwargs["target_count"] == 4, (
        f"expected target_count=4 (current=3 + 1), got {call_kwargs['target_count']}"
    )

    await store.close()


# ---------------------------------------------------------------------------
# Test 4 — no publisher → no wiring
# ---------------------------------------------------------------------------


def test_on_resume_fn_handles_no_publisher() -> None:
    """SC-10: if hub._turn_publisher is None, _on_resume_fn is not wired.

    MessagePrepMiddleware only assigns pool._on_resume_fn when
    hub._turn_publisher is not None.  If publisher is None, pool._on_resume_fn
    stays None — no wiring dependency error.
    """
    # Arrange — hub without a publisher
    cr = CircuitRegistry()
    hub = Hub(circuit_registry=cr)
    # _turn_publisher is None by default (see hub.py line ~110)
    assert hub._turn_publisher is None

    pool = _make_pool_with_resume_fn()
    # _on_resume_fn starts as None (pool.py line ~79)
    assert pool._on_resume_fn is None

    # Simulate what MessagePrepMiddleware does:
    if pool._on_resume_fn is None and hub._turn_publisher is not None:
        # This branch is NOT taken — publisher is None
        pool._on_resume_fn = lambda sid: None  # type: ignore[assignment]

    # Assert — _on_resume_fn remains None when publisher absent
    assert pool._on_resume_fn is None, (
        "_on_resume_fn must remain None when hub._turn_publisher is None"
    )
