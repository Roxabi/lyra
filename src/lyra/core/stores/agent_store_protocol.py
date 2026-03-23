"""Full AgentStoreProtocol + make_agent_store factory.

The narrow ``AgentStoreProtocol`` in ``agent_seeder.py`` covers only ``get``
and ``upsert`` — just enough for TOML seeding.  This module provides a fuller
protocol covering every method callers depend on, plus a factory function that
reads ``LYRA_DB`` to select the backing implementation at runtime.

Typical test usage::

    import os, pytest
    os.environ["LYRA_DB"] = "json"
    store = make_agent_store()          # → JsonAgentStore
    await store.connect()

Production code (cli_agent, bootstrap) continues to instantiate AgentStore
directly — they are not changed by this issue.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..agent_models import AgentRow, AgentRuntimeStateRow

if TYPE_CHECKING:
    from .agent_store import AgentStore
    from .json_agent_store import JsonAgentStore

__all__ = ["AgentStoreProtocol", "make_agent_store"]


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


def make_agent_store(
    db_path: Path | None = None,
) -> "AgentStore | JsonAgentStore":
    """Return the appropriate agent store based on the ``LYRA_DB`` env var.

    ``LYRA_DB=json``  →  :class:`~lyra.core.json_agent_store.JsonAgentStore`
                          Path: ``LYRA_AGENT_STORE_PATH`` or
                          ``~/.lyra/agents_test.json``

    Any other value (or unset)  →  :class:`~lyra.core.agent_store.AgentStore`
                                    Path: *db_path* or ``~/.lyra/auth.db``

    Note: the returned store is not yet connected — callers must ``await
    store.connect()`` before use.
    """
    if os.environ.get("LYRA_DB") == "json":
        from .json_agent_store import JsonAgentStore

        store_path_env = os.environ.get("LYRA_AGENT_STORE_PATH")
        path = (
            Path(store_path_env)
            if store_path_env
            else Path.home() / ".lyra" / "agents_test.json"
        )
        return JsonAgentStore(path=path)

    from .agent_store import AgentStore

    resolved = db_path or (Path.home() / ".lyra" / "auth.db")
    return AgentStore(db_path=resolved)
