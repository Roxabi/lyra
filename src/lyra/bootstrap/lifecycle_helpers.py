"""Shared lifecycle helpers for multibot and standalone Hub bootstrap."""
from __future__ import annotations

import asyncio
import logging
import signal

log = logging.getLogger(__name__)


def setup_signal_handlers(stop: asyncio.Event) -> None:
    """Register SIGINT/SIGTERM handlers on the running event loop."""
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)


async def teardown_buses(*buses: object) -> None:
    """Stop all provided buses (Bus[T] instances)."""
    for bus in buses:
        await bus.stop()


async def teardown_dispatchers(dispatchers: list) -> None:
    """Stop all outbound dispatchers."""
    for d in dispatchers:
        await d.stop()
