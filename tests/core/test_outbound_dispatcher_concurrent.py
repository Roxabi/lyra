"""Tests for OutboundDispatcher: concurrent per-scope dispatch (#364).

T1 [RED]: different scopes dispatch concurrently — fails on old sequential code,
          passes once create_task fan-out (S1+S2) is implemented.
T2 [RED]: same-scope FIFO order preserved — will fail if per-scope lock breaks
          ordering; `out.text` access also catches attribute regressions.
T3 [GREEN]: _scope_locks emptied after stop().
T4 [GREEN]: stop() drains in-flight tasks before returning.
"""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import AsyncMock, MagicMock

from lyra.core.hub.outbound_dispatcher import OutboundDispatcher
from lyra.core.messaging.message import InboundMessage, OutboundMessage
from tests.conftest import yield_once

from .conftest import make_dispatcher_msg


def _make_msg(scope_id: str) -> InboundMessage:
    """Create a test InboundMessage with the given scope_id."""
    return dataclasses.replace(make_dispatcher_msg(), scope_id=scope_id)


# ---------------------------------------------------------------------------
# T1 — RED: different scopes must dispatch concurrently
# ---------------------------------------------------------------------------


async def test_concurrent_different_scopes() -> None:
    """Two items from different scopes dispatch concurrently (Event-based).

    Each send sets a done_event on completion. Both events must fire within
    0.19s — well above a single 100ms send (concurrent path) but well below
    two sequential 100ms sends (~200ms). This avoids the flaky wall-clock
    ceiling while still distinguishing concurrent from sequential execution.

    [RED against old sequential implementation, GREEN with new fan-out]
    """
    # Arrange
    adapter = MagicMock()
    done_a: asyncio.Event = asyncio.Event()
    done_b: asyncio.Event = asyncio.Event()

    async def slow_send(msg: InboundMessage, out: OutboundMessage) -> None:
        await asyncio.sleep(0.1)
        if out.to_text() == "a":
            done_a.set()
        else:
            done_b.set()

    adapter.send = AsyncMock(side_effect=slow_send)
    dispatcher = OutboundDispatcher(platform_name="discord", adapter=adapter)
    await dispatcher.start()

    try:
        msg_a = _make_msg("channel:A")
        msg_b = _make_msg("channel:B")

        # Act
        dispatcher.enqueue(msg_a, OutboundMessage.from_text("a"))
        dispatcher.enqueue(msg_b, OutboundMessage.from_text("b"))

        # Assert — both events must fire within 190ms (concurrent ~100ms,
        # sequential ~200ms). TimeoutError means sequential dispatch.
        await asyncio.wait_for(
            asyncio.gather(done_a.wait(), done_b.wait()),
            timeout=0.19,
        )
    finally:
        await dispatcher.stop()


# ---------------------------------------------------------------------------
# T2 — FIFO within same scope
# ---------------------------------------------------------------------------


async def test_fifo_same_scope() -> None:
    """Three items for the same scope are delivered in enqueue order.

    The per-scope asyncio.Lock serializes same-scope sends. Even with concurrent
    fan-out infrastructure, FIFO ordering within a scope must be preserved.
    """
    # Arrange
    call_order: list[str] = []
    adapter = MagicMock()

    async def track_send(msg: InboundMessage, out: OutboundMessage) -> None:
        # OutboundMessage has no .text — use to_text() to flatten content parts.
        call_order.append(out.to_text())
        await asyncio.sleep(0.03)

    adapter.send = AsyncMock(side_effect=track_send)
    dispatcher = OutboundDispatcher(platform_name="discord", adapter=adapter)
    await dispatcher.start()

    try:
        msg = _make_msg("channel:A")

        # Act — enqueue three messages for the same scope
        for label in ("msg1", "msg2", "msg3"):
            dispatcher.enqueue(msg, OutboundMessage.from_text(label))

        # Wait for queue to drain, then await any in-flight scope tasks.
        await dispatcher._queue.join()
        if dispatcher._scope_tasks:
            await asyncio.gather(*list(dispatcher._scope_tasks), return_exceptions=True)

        # Assert — FIFO order preserved
        assert call_order == ["msg1", "msg2", "msg3"], (
            f"Expected FIFO order for same scope, got: {call_order}"
        )
    finally:
        await dispatcher.stop()


# ---------------------------------------------------------------------------
# T3 — GREEN: _scope_locks cleaned up after stop()
# ---------------------------------------------------------------------------


async def test_scope_locks_cleanup() -> None:
    """After stop(), _scope_locks must be empty (reaper removes idle locks).

    _reap_scope_locks() is called inside stop() and removes entries where
    lock.locked() is False. After all sends complete and stop() drains scope
    tasks, the dict must be empty.

    [GREEN: _reap_scope_locks() already implemented in stop()]
    """
    # Arrange
    adapter = MagicMock()
    adapter.send = AsyncMock()
    dispatcher = OutboundDispatcher(platform_name="discord", adapter=adapter)
    await dispatcher.start()

    for scope in ("scope:X", "scope:Y", "scope:Z"):
        msg = _make_msg(scope)
        dispatcher.enqueue(msg, OutboundMessage.from_text("x"))

    # Wait for queue to drain so locks are released before stop().
    await dispatcher._queue.join()
    await yield_once()  # yield to event loop for scope task cleanup

    # Act
    await dispatcher.stop()

    # Assert — all locks reaped (none held at stop time)
    assert len(dispatcher._scope_locks) == 0, (
        f"Expected empty _scope_locks after stop(), "
        f"got {len(dispatcher._scope_locks)} entries: "
        f"{list(dispatcher._scope_locks.keys())}"
    )


# ---------------------------------------------------------------------------
# T4 — GREEN: stop() drains in-flight tasks before returning
# ---------------------------------------------------------------------------


async def test_stop_drains_tasks() -> None:
    """stop() must await all in-flight scope tasks, not abandon them mid-send.

    A 100ms send is started, then stop() is called after 10ms (still running).
    stop() must block until the send completes — completed must contain "done"
    before stop() returns.

    [GREEN: stop() already gathers _scope_tasks]
    """
    # Arrange
    completed: list[str] = []
    adapter = MagicMock()

    async def slow_send(msg: InboundMessage, out: OutboundMessage) -> None:
        await asyncio.sleep(0.1)
        completed.append("done")

    adapter.send = AsyncMock(side_effect=slow_send)
    dispatcher = OutboundDispatcher(platform_name="discord", adapter=adapter)
    await dispatcher.start()

    # Act
    dispatcher.enqueue(make_dispatcher_msg(), OutboundMessage.from_text("x"))
    await asyncio.sleep(0.01)  # let the worker dequeue and start the scope task
    await dispatcher.stop()  # must wait for the 100ms send to finish

    # Assert — send completed before stop() returned
    assert completed == ["done"], (
        f"stop() did not drain in-flight scope task: {completed}"
    )
