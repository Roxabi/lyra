"""Shared lifecycle helpers for multibot and standalone Hub bootstrap."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Sequence
from typing import Any, Protocol

log = logging.getLogger(__name__)


class _Stoppable(Protocol):
    async def stop(self) -> Any: ...


def setup_signal_handlers(stop: asyncio.Event) -> None:
    """Register SIGINT/SIGTERM handlers on the running event loop."""
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)


async def close_safely(label: str, *awaitables: Awaitable[Any]) -> None:
    """Close/stop resources concurrently, logging failures without re-raising.

    Accepts any Awaitable (coroutine, Task, Future). Skips BaseException
    subclasses that are not Exception (CancelledError, KeyboardInterrupt,
    SystemExit) — normal shutdown path. All resources are attempted regardless
    of individual failures.
    """
    if not awaitables:
        return
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.exception("Close failed [%s]", label, exc_info=r)
        elif isinstance(r, BaseException):
            log.debug("Close cancelled [%s] — resource may not be fully closed", label)


async def teardown_buses(*buses: _Stoppable) -> None:
    """Stop all provided buses (Bus[T] instances)."""
    for bus in buses:
        await bus.stop()


async def teardown_dispatchers(dispatchers: Sequence[_Stoppable]) -> None:
    """Stop all outbound dispatchers."""
    for d in dispatchers:
        await d.stop()
