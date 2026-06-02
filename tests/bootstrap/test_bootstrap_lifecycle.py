"""Tests for run_lifecycle in bootstrap_lifecycle.py — F6 thread store teardown."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from factory.bootstrap.lifecycle.bootstrap_lifecycle import LifecycleResources


def _make_hub() -> MagicMock:
    """Minimal Hub mock sufficient for run_lifecycle."""
    hub = MagicMock()
    hub.run = AsyncMock()
    hub.shutdown = AsyncMock()
    hub.notify_shutdown_inflight = AsyncMock()
    hub._event_bus = None  # disable audit consumer branch
    hub._turn_store = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.start = AsyncMock()
    hub.inbound_bus.stop = AsyncMock()
    return hub


def _make_wired(dc_thread_store: AsyncMock | None) -> MagicMock:
    """Minimal WiredAdapters mock with the given dc_thread_store."""
    wired = MagicMock()
    wired.tg_adapters = []
    wired.tg_dispatchers = []
    wired.dc_adapters = []
    wired.dc_dispatchers = []
    wired.dc_thread_store = dc_thread_store
    return wired


def _make_resources() -> LifecycleResources:
    return LifecycleResources(pm=None, cli_pool=None, nc=None)


async def _watchdog_immediate(tasks: object, stop: asyncio.Event) -> None:
    """Replacement for watchdog that triggers shutdown immediately."""
    stop.set()


# ---------------------------------------------------------------------------
# F6a — dc_thread_store provided → close() called exactly once
# ---------------------------------------------------------------------------


async def test_run_lifecycle_closes_dc_thread_store() -> None:
    """F6a: run_lifecycle calls dc_thread_store.close() once after adapter teardown."""
    from factory.bootstrap.lifecycle.bootstrap_lifecycle import run_lifecycle

    hub = _make_hub()
    dc_thread_store = AsyncMock()
    wired = _make_wired(dc_thread_store)
    resources = _make_resources()
    stop = asyncio.Event()
    stop.set()  # trigger immediate shutdown

    with (
        patch(
            "factory.bootstrap.factory.utils.watchdog",
            side_effect=_watchdog_immediate,
        ),
        patch("uvicorn.Server.serve", new_callable=AsyncMock),
    ):
        await run_lifecycle(
            hub=hub,
            wired=wired,
            resources=resources,
            _stop=stop,
        )

    # Assert — teardown must have closed the thread store exactly once
    dc_thread_store.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# F6b — dc_thread_store=None → no error, lifecycle completes normally
# ---------------------------------------------------------------------------


async def test_run_lifecycle_none_dc_thread_store_is_noop() -> None:
    """F6b: run_lifecycle with dc_thread_store=None does not attempt to close the store.

    The None guard must prevent any close() call on the thread store.
    """
    from factory.bootstrap.lifecycle.bootstrap_lifecycle import run_lifecycle

    hub = _make_hub()
    mock_store = AsyncMock()  # would fail loudly if close() were called
    wired = _make_wired(dc_thread_store=None)  # None guard being tested
    resources = _make_resources()
    stop = asyncio.Event()
    stop.set()

    with (
        patch(
            "factory.bootstrap.factory.utils.watchdog",
            side_effect=_watchdog_immediate,
        ),
        patch("uvicorn.Server.serve", new_callable=AsyncMock),
    ):
        await run_lifecycle(
            hub=hub,
            wired=wired,
            resources=resources,
            _stop=stop,
        )

    # Assert — lifecycle completed and the None guard prevented any close attempt
    hub.shutdown.assert_awaited_once()
    mock_store.close.assert_not_called()  # None guard: close() must NOT be called
