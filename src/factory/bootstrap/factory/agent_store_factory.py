"""Bootstrap factory for AgentStore — selects implementation via FACTORY_DB env var."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from factory.paths import factory_data_dir

if TYPE_CHECKING:
    from factory.core.stores.json_agent_store import JsonAgentStore
    from factory.infrastructure.stores.agent_store import AgentStore

__all__ = ["make_agent_store"]


def make_agent_store(
    db_path: Path | None = None,
) -> "AgentStore | JsonAgentStore":
    """Return the appropriate agent store based on the ``FACTORY_DB`` env var.

    ``FACTORY_DB=json`` → :class:`~factory.core.stores.json_agent_store.JsonAgentStore`
                          Path: ``FACTORY_AGENT_STORE_PATH`` or
                          ``~/.roxabi/factory/agents_test.json``

    Any other value (or unset)  →
        :class:`~factory.infrastructure.stores.agent_store.AgentStore`
                                    Path: *db_path* or ``~/.roxabi/factory/config.db``

    Note: the returned store is not yet connected — callers must ``await
    store.connect()`` before use.
    """
    if os.environ.get("FACTORY_DB") == "json":
        from factory.core.stores.json_agent_store import JsonAgentStore

        store_path_env = os.environ.get("FACTORY_AGENT_STORE_PATH")
        _vault = factory_data_dir().resolve()
        path = Path(store_path_env) if store_path_env else _vault / "agents_test.json"
        return JsonAgentStore(path=path)

    from factory.infrastructure.stores.agent_store import AgentStore

    _vault = factory_data_dir().resolve()
    resolved = db_path or (_vault / "config.db")
    return AgentStore(db_path=resolved)
