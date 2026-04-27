"""AgentStoreProtocol — structural interface for agent stores.

The narrow ``AgentStoreProtocol`` in ``agent_seeder.py`` covers only ``get``
and ``upsert`` — just enough for TOML seeding.  This module provides a fuller
protocol covering every method callers depend on.

Factory: use ``lyra.bootstrap.factory.agent_store_factory.make_agent_store``
to obtain a store instance at runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..agent.agent_models import AgentRow, AgentRuntimeStateRow

__all__ = ["AgentStoreProtocol"]


@runtime_checkable
class AgentStoreProtocol(Protocol):
    """Full structural interface shared by AgentStore and JsonAgentStore.

    Callers that depend only on the protocol (and not on SQLite internals)
    can type-hint against this class so that JsonAgentStore can be swapped in
    transparently for testing.
    """

    # Lifecycle
    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    # Sync reads
    def get(self, name: str) -> AgentRow | None: ...
    def get_all(self) -> list[AgentRow]: ...
    def get_bot_agent(self, platform: str, bot_id: str) -> str | None: ...
    def get_all_bot_mappings(self) -> dict[tuple[str, str], str]: ...
    def get_bot_settings(self, platform: str, bot_id: str) -> dict: ...

    # Async writes
    async def upsert(self, row: AgentRow) -> None: ...
    async def delete(self, name: str) -> None: ...
    async def set_bot_agent(
        self,
        platform: str,
        bot_id: str,
        agent_name: str,
        *,
        settings: dict | None = None,
    ) -> None: ...
    async def set_bot_settings(
        self, platform: str, bot_id: str, settings: dict
    ) -> None: ...
    async def remove_bot_agent(self, platform: str, bot_id: str) -> None: ...

    # Runtime state
    async def get_all_runtime_states(self) -> dict[str, AgentRuntimeStateRow]: ...
    async def set_runtime_state(
        self, agent_name: str, status: str, pool_count: int = 0
    ) -> None: ...

    # TOML seeding
    async def seed_from_toml(self, path: Path, *, force: bool = False) -> int: ...
