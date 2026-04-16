"""Signal handler setup for standalone adapter processes."""

from __future__ import annotations

import asyncio
import signal


def setup_shutdown_event(_stop: asyncio.Event | None = None) -> asyncio.Event:
    """Create and configure shutdown event with signal handlers.

    Args:
        _stop: Optional event for graceful shutdown (tests inject this).

    Returns:
        The stop event to wait on. If _stop is provided, returns it unchanged.
        Otherwise creates a new event and registers SIGTERM/SIGINT handlers.
    """
    stop = _stop if _stop is not None else asyncio.Event()
    if _stop is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    return stop
