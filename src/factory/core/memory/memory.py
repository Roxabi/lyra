"""Memory layer for factory — agent long-term recall (legacy MemoryManager).

Historically backed by ``roxabi-vault.AsyncMemoryDB``. That package is removed;
knowledge capture/search is now the cortex NATS satellite
(``factory.integrations.cortex_vault.CortexVault`` / ADR-087).

``MemoryManager`` remains as an optional DI hook for hub/agent recall code
paths, but constructing it raises until a cortex-backed implementation lands.
SessionSnapshot and freshness helpers stay usable without any vault package.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.infrastructure.stores.identity.identity_alias_store import (
        IdentityAliasStore,
    )

from factory.core.memory.memory_freshness import age_str, is_stale
from factory.core.memory.memory_types import FRESHNESS_TTL_DAYS, SessionSnapshot
from factory.core.memory.memory_upserts import MemoryManagerUpserts

# Re-export so `from factory.core.memory.memory import SessionSnapshot` keeps working.
__all__ = [
    "FRESHNESS_TTL_DAYS",
    "MemoryManager",
    "SessionSnapshot",
    "age_str",
    "is_stale",
]

log = logging.getLogger(__name__)

_REMOVED_MSG = (
    "MemoryManager (roxabi-vault AsyncMemoryDB) was removed. "
    "Knowledge I/O uses cortex-memory via CortexVault (NATS). "
    "Agent long-term assemble is not wired yet — see ADR-087."
)


class MemoryManager(MemoryManagerUpserts):
    """Retired vault-backed memory manager — construction is blocked.

    Hub/agent still accept ``_memory: MemoryManager | None`` (always None in
    production). Do not instantiate until a cortex-backed replacement exists.
    """

    def __init__(self, vault_path: Path | str) -> None:  # noqa: ARG002
        raise RuntimeError(_REMOVED_MSG)

    def set_alias_store(self, store: IdentityAliasStore) -> None:
        raise RuntimeError(_REMOVED_MSG)

    async def connect(self) -> None:
        raise RuntimeError(_REMOVED_MSG)

    async def close(self) -> None:
        raise RuntimeError(_REMOVED_MSG)

    async def get_identity_anchor(self, namespace: str) -> str | None:  # noqa: ARG002
        raise RuntimeError(_REMOVED_MSG)

    async def recall(
        self,
        user_id: str,
        namespace: str,
        first_msg: str = "",
        token_budget: int = 0,
    ) -> str:
        raise RuntimeError(_REMOVED_MSG)
