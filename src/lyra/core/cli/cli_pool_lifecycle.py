"""Lifecycle mixin for CliPool — split from cli_pool.py (#760).

Provides start(), stop(), drain(), and get_reaper_status() for CliPool.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, cast

from .cli_pool_types import _CliPoolCore

if TYPE_CHECKING:
    from .cli_pool_entry import _ProcessEntry

log = logging.getLogger(__name__)


class CliPoolLifecycleMixin:
    """Mixin providing start/stop/drain lifecycle methods for CliPool."""

    # Declared for type-checking — initialised by CliPool.__init__.
    if TYPE_CHECKING:
        _entries: dict[str, _ProcessEntry]
        _reaper_task: asyncio.Task[None] | None
        _last_sweep_at: float | None
        _idle_ttl: int
        _reaper_interval: int

    async def start(self) -> None:
        """Start the idle reaper background task."""
        self._reaper_task = asyncio.create_task(
            cast(_CliPoolCore, self)._idle_reaper()
        )
        log.info("CliPool started (idle_ttl=%ds)", self._idle_ttl)

    def get_reaper_status(self) -> dict[str, bool | float | None]:
        """Return reaper task status for health monitoring.

        Returns a dict with:
            - alive: whether the reaper task is running
            - last_sweep_age: seconds since last sweep, or None
        """
        return {
            "alive": (self._reaper_task is not None and not self._reaper_task.done()),
            "last_sweep_age": (
                round(time.monotonic() - self._last_sweep_at, 1)
                if self._last_sweep_at is not None
                else None
            ),
        }

    async def drain(self, timeout: float = 60.0) -> None:
        """Wait for all in-flight turns to complete before stopping.

        A turn is considered in-flight when its ``_lock`` is held.  Idle
        processes (alive but waiting for the next message) are not counted —
        they are safe to kill immediately.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            inflight = [pid for pid, e in self._entries.items() if e._lock.locked()]
            if not inflight:
                return
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                log.warning(
                    "CliPool drain timeout — %d turn(s) still in-flight: %s",
                    len(inflight),
                    inflight,
                )
                return
            log.info(
                "CliPool draining — %d turn(s) in-flight, %.0fs remaining…",
                len(inflight),
                remaining,
            )
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        """Stop reaper and kill all processes."""
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
        for pool_id in list(self._entries):
            await cast(_CliPoolCore, self)._kill(pool_id)
        log.info("CliPool stopped")
