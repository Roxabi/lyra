"""Bootstrap utilities — shared helpers for startup wiring."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

log = logging.getLogger(__name__)


def _log_task_failure(task: asyncio.Task[object]) -> None:
    """Done callback — logs adapter task failures (e.g. Discord LoginFailure)."""
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        log.error(
            "Adapter task '%s' exited with error: %s",
            task.get_name(),
            exc,
            exc_info=exc,
        )


async def watchdog(
    tasks: Sequence[asyncio.Task[object]],
    stop: asyncio.Event,
) -> None:
    """Monitor long-running tasks; trigger graceful shutdown if any exits unexpectedly.

    Replaces a bare ``stop.wait()`` so that a silently-dying task no longer
    leaves the process running in a degraded state.
    """
    if not tasks:
        log.warning("watchdog: no tasks to monitor — waiting for stop signal.")
        await stop.wait()
        return

    pending: set[asyncio.Task[object]] = set(tasks)
    stop_task: asyncio.Task[object] = asyncio.create_task(
        stop.wait(), name="watchdog-stop-sentinel"
    )
    pending.add(stop_task)

    try:
        while len(pending) > 1:  # more than just the stop sentinel
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )

            if stop_task in done:
                # Normal shutdown (SIGINT / SIGTERM) — let caller tear down.
                return

            for task in done:
                name = task.get_name()
                if task.cancelled():
                    log.warning("Task '%s' was cancelled unexpectedly.", name)
                else:
                    exc = task.exception()
                    if exc is not None:
                        log.error(
                            "Task '%s' crashed — triggering shutdown: %s",
                            name,
                            exc,
                            exc_info=exc,
                        )
                    else:
                        log.error(
                            "Task '%s' exited unexpectedly — triggering shutdown.",
                            name,
                        )

            log.info("Watchdog: requesting graceful shutdown.")
            stop.set()
            return
    finally:
        if not stop_task.done():
            stop_task.cancel()
            try:
                await stop_task
            except asyncio.CancelledError:
                pass
