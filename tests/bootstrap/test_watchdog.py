"""Tests for bootstrap.utils.watchdog (#195)."""

from __future__ import annotations

import asyncio

import pytest

from lyra.bootstrap.utils import watchdog


@pytest.fixture()
def stop() -> asyncio.Event:
    return asyncio.Event()


# ---------------------------------------------------------------------------
# Normal shutdown via stop event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_returns_on_stop_event(stop: asyncio.Event) -> None:
    """Watchdog should return cleanly when stop is set externally (SIGINT/SIGTERM)."""

    async def _hang_forever() -> None:
        await asyncio.Event().wait()

    async def _set_stop_soon() -> None:
        await asyncio.sleep(0)  # yield once so watchdog enters the loop
        stop.set()

    task = asyncio.create_task(_hang_forever(), name="forever")
    trigger = asyncio.create_task(_set_stop_soon())
    try:
        await asyncio.wait_for(watchdog([task], stop), timeout=2.0)
        await trigger

        # Watchdog should not cancel monitored tasks — that's the caller's job.
        assert not task.done()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Task crashes → shutdown triggered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_triggers_shutdown_on_task_crash(stop: asyncio.Event) -> None:
    """If a monitored task raises, watchdog should set the stop event."""

    async def _crash() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(_crash(), name="crasher")
    await asyncio.wait_for(watchdog([task], stop), timeout=2.0)

    assert stop.is_set()


# ---------------------------------------------------------------------------
# Task exits cleanly but unexpectedly → shutdown triggered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_triggers_shutdown_on_clean_exit(stop: asyncio.Event) -> None:
    """A task that returns without error is still unexpected — watchdog shuts down."""

    async def _exit_early() -> None:
        return

    task = asyncio.create_task(_exit_early(), name="early-exit")
    await asyncio.wait_for(watchdog([task], stop), timeout=2.0)

    assert stop.is_set()


# ---------------------------------------------------------------------------
# Task cancelled unexpectedly → shutdown triggered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_triggers_shutdown_on_unexpected_cancel(
    stop: asyncio.Event,
) -> None:
    """A task cancelled without stop being set is still unexpected."""

    async def _hang_forever() -> None:
        await asyncio.Event().wait()

    async def _cancel_soon(t: asyncio.Task[None]) -> None:
        await asyncio.sleep(0)  # yield once so watchdog enters the loop
        t.cancel()

    task = asyncio.create_task(_hang_forever(), name="to-cancel")
    asyncio.create_task(_cancel_soon(task))
    await asyncio.wait_for(watchdog([task], stop), timeout=2.0)

    assert stop.is_set()


# ---------------------------------------------------------------------------
# Multiple tasks — first crash triggers shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_first_crash_among_many(stop: asyncio.Event) -> None:
    """With multiple tasks, the first to crash triggers shutdown."""

    async def _hang_forever() -> None:
        await asyncio.Event().wait()

    async def _crash_soon() -> None:
        await asyncio.sleep(0)  # yield once so watchdog enters the loop
        raise RuntimeError("first down")

    healthy = asyncio.create_task(_hang_forever(), name="healthy")
    crasher = asyncio.create_task(_crash_soon(), name="crasher")
    try:
        await asyncio.wait_for(watchdog([healthy, crasher], stop), timeout=2.0)
    finally:
        healthy.cancel()
        try:
            await healthy
        except asyncio.CancelledError:
            pass

    assert stop.is_set()


# ---------------------------------------------------------------------------
# Stop sentinel task is cleaned up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_cleans_up_sentinel_on_crash(stop: asyncio.Event) -> None:
    """The internal stop-sentinel task should not leak after watchdog returns."""

    async def _crash() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(_crash(), name="crasher")
    await asyncio.wait_for(watchdog([task], stop), timeout=2.0)

    # Give the event loop a tick to process cancellations.
    await asyncio.sleep(0)

    # No lingering tasks named watchdog-stop-sentinel.
    for t in asyncio.all_tasks():
        assert t.get_name() != "watchdog-stop-sentinel"


# ---------------------------------------------------------------------------
# Empty task list — falls back to stop.wait()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_empty_task_list_waits_for_stop(stop: asyncio.Event) -> None:
    """With no tasks, watchdog should wait for the stop event instead of returning."""

    async def _set_stop_soon() -> None:
        await asyncio.sleep(0)
        stop.set()

    asyncio.create_task(_set_stop_soon())
    await asyncio.wait_for(watchdog([], stop), timeout=2.0)

    assert stop.is_set()


# ---------------------------------------------------------------------------
# Stop already set before watchdog starts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_stop_already_set(stop: asyncio.Event) -> None:
    """If stop is pre-set, watchdog should return immediately without error."""

    async def _hang_forever() -> None:
        await asyncio.Event().wait()

    stop.set()
    task = asyncio.create_task(_hang_forever(), name="forever")
    try:
        await asyncio.wait_for(watchdog([task], stop), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Watchdog returned without triggering any additional side effects.
    assert stop.is_set()


# ---------------------------------------------------------------------------
# Simultaneous task completions in same done batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_simultaneous_crashes(stop: asyncio.Event) -> None:
    """Two tasks that crash immediately may land in the same done batch."""

    async def _crash_a() -> None:
        raise RuntimeError("crash-a")

    async def _crash_b() -> None:
        raise RuntimeError("crash-b")

    task_a = asyncio.create_task(_crash_a(), name="crash-a")
    task_b = asyncio.create_task(_crash_b(), name="crash-b")
    await asyncio.wait_for(watchdog([task_a, task_b], stop), timeout=2.0)

    assert stop.is_set()
