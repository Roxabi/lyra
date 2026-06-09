"""ThreadStoreProtocol — structural protocol for Discord thread persistence.

Decouples the Discord adapter from the concrete SQLite ThreadStore (ADR-059 V5).
Implementations live in factory.infrastructure.stores; this protocol lives in core.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ThreadStoreProtocol(Protocol):
    """Structural protocol for Discord thread ownership persistence."""

    async def get_thread_ids(
        self,
        bot_id: str,
        active_since: datetime | None = None,
    ) -> list[str]: ...

    async def is_owned(self, thread_id: str, bot_id: str) -> bool: ...

    async def claim(
        self,
        thread_id: str,
        bot_id: str,
        channel_id: str,
        guild_id: str | None = None,
    ) -> None: ...

    async def release(self, thread_id: str, bot_id: str) -> None: ...
