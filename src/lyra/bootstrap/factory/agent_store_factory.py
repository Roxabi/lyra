"""Bootstrap factory for AgentStore — selects implementation via LYRA_DB env var."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.stores.json_agent_store import JsonAgentStore
    from lyra.infrastructure.stores.agent_store import AgentStore

__all__ = ["make_agent_store"]


def make_agent_store(
    db_path: Path | None = None,
) -> "AgentStore | JsonAgentStore":
    """Return the appropriate agent store based on the ``LYRA_DB`` env var.

    ``LYRA_DB=json``  →  :class:`~lyra.core.stores.json_agent_store.JsonAgentStore`
                          Path: ``LYRA_AGENT_STORE_PATH`` or
                          ``~/.lyra/agents_test.json``

    Any other value (or unset)  →
        :class:`~lyra.infrastructure.stores.agent_store.AgentStore`
                                    Path: *db_path* or ``~/.lyra/config.db``

    Note: the returned store is not yet connected — callers must ``await
    store.connect()`` before use.
    """
    if os.environ.get("LYRA_DB") == "json":
        from lyra.core.stores.json_agent_store import JsonAgentStore

        store_path_env = os.environ.get("LYRA_AGENT_STORE_PATH")
        _vault = Path(
            os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))
        ).resolve()
        path = Path(store_path_env) if store_path_env else _vault / "agents_test.json"
        return JsonAgentStore(path=path)

    from lyra.infrastructure.stores.agent_store import AgentStore

    _vault = Path(
        os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))
    ).resolve()
    resolved = db_path or (_vault / "config.db")
    return AgentStore(db_path=resolved)
