"""Bootstrap utilities — shared helpers for startup wiring."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


def _log_task_failure(task: asyncio.Task) -> None:  # type: ignore[type-arg]
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
