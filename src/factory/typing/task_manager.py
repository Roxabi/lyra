"""TypingTaskManager — per-channel typing indicator background task lifecycle.

Relocated from factory.adapters.shared._shared to factory.typing so that
factory.inbound can reference it without violating the inbound-no-adapters
importlinter contract (ADR-073 / #1666).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


class TypingTaskManager:
    """Manages per-channel typing indicator background tasks.

    Extracted from TelegramAdapter and DiscordAdapter to eliminate identical
    task-management logic. Each adapter keeps its own typing coroutine factory;
    this class only manages the task dict lifecycle.
    """

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def start(
        self,
        target: int,
        coro_factory: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Cancel any existing task for *target* and start a new one."""
        existing = self._tasks.pop(target, None)
        if existing and not existing.done():
            existing.cancel()
        self._tasks[target] = asyncio.create_task(
            coro_factory(),
            name=f"typing:{target}",
        )

    def cancel(self, target: int) -> None:
        """Cancel and remove the typing task for *target* (no-op if absent)."""
        task = self._tasks.pop(target, None)
        if task and not task.done():
            task.cancel()

    async def cancel_all(self) -> None:
        """Cancel all pending typing tasks and await their completion."""
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
