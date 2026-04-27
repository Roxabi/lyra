"""ThreadStoreProtocol — structural protocol for Discord thread persistence.

Decouples the Discord adapter from the concrete SQLite ThreadStore (ADR-059 V5).
Implementations live in lyra.infrastructure.stores; this protocol lives in core.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ThreadStoreProtocol(Protocol):
    """Structural protocol for Discord thread ownership and session persistence."""

    async def close(self) -> None: ...

    async def get_thread_ids(
        self,
        bot_id: str,
        active_since: datetime | None = None,
    ) -> list[str]: ...

    async def is_owned(self, thread_id: str, bot_id: str) -> bool: ...

    async def get_session(
        self, thread_id: str, bot_id: str
    ) -> tuple[str | None, str | None]: ...

    async def claim(
        self,
        thread_id: str,
        bot_id: str,
        channel_id: str,
        guild_id: str | None = None,
    ) -> None: ...

    async def update_session(
        self, thread_id: str, bot_id: str, session_id: str, pool_id: str
    ) -> None: ...
