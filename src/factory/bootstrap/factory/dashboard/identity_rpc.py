"""Hub RPC: rewarm UserStore identity cache after control-plane link mutations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

log = logging.getLogger(__name__)


async def handle_identity_cache_rewarm(
    hub: Hub, _nc: NATS, _payload: dict[str, Any]
) -> dict[str, Any]:
    """Reload platform_identities into UserStore after BFF link/unlink."""
    user_store = getattr(hub, "_user_store", None)
    if user_store is None or not hasattr(user_store, "rewarm_identity_cache"):
        return {"ok": True, "rewarmed": False}
    await user_store.rewarm_identity_cache()
    log.debug("UserStore identity cache rewarmed via dashboard RPC")
    return {"ok": True, "rewarmed": True}
