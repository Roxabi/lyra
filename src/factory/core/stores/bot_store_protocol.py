"""BotStoreProtocol — structural interface for bot stores.

Factory: use ``factory.bootstrap.bootstrap_stores.open_stores``
to obtain a store bundle (which includes the BotStore) at runtime.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..agent.bot_models import BotRow

__all__ = ["BotStoreProtocol"]


@runtime_checkable
class BotStoreProtocol(Protocol):
    """Full structural interface shared by BotStore implementations.

    Callers that depend only on the protocol (and not on SQLite internals)
    can type-hint against this class so that alternative stores can be swapped in
    transparently for testing.
    """

    # Lifecycle
    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    # Sync reads
    def get(self, platform: str, bot_id: str) -> BotRow | None: ...
    def get_all(self) -> list[BotRow]: ...

    # Async writes
    async def upsert(self, row: BotRow) -> None: ...
    async def delete(self, platform: str, bot_id: str) -> None: ...
