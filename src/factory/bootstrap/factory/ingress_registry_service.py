"""Hub-side access to ingress.db installation registry (ADR-096)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from factory.infrastructure.stores.ingress.installation_store import (
    InstallationStore,
    default_db_path,
)

if TYPE_CHECKING:
    from factory.infrastructure.stores.ingress.installation_store import (
        InstallationStore as InstallationStoreType,
    )

log = logging.getLogger(__name__)

_store: InstallationStoreType | None = None


async def get_installation_store() -> InstallationStoreType:
    global _store
    if _store is None:
        _store = InstallationStore(default_db_path())
        await _store.connect()
        log.info("ingress_registry_service connected db=%s", default_db_path())
    return _store


async def close_installation_store() -> None:
    global _store
    if _store is not None:
        await _store.close()
        _store = None