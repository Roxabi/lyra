"""Hub principal rehydrate — ignore client roles (ADR-103 Slice 3).

Proof-bound only: session_token or api_key required. Bare principal_user_id
is never enough (prevents NATS impersonation of any active user).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from factory.core.auth.control_plane import ControlPlanePrincipal

if TYPE_CHECKING:
    from factory.core.hub import Hub

log = logging.getLogger(__name__)

__all__ = ["rehydrate_principal"]


async def rehydrate_principal(
    hub: Hub,
    *,
    wire: ControlPlanePrincipal | None,
    payload: dict[str, Any],
) -> ControlPlanePrincipal | None:
    """Return store-backed principal; never trust wire roles/orgs.

    Proof (required — fail-closed):
    1. ``session_token`` → ``resolve_session`` (roles from store)
    2. ``api_key`` → ``resolve_api_key`` (roles from store)

    Optional: if *wire* carries ``user_id``, it must match the proof principal.
    Wire roles/orgs are ignored always.
    """
    cp = getattr(hub, "_control_plane", None)
    if cp is None:
        log.warning("rehydrate_deny reason=no_control_plane")
        return None

    session_token = payload.get("session_token")
    if isinstance(session_token, str) and session_token.strip():
        principal = await cp.resolve_session(session_token.strip())
        if principal is None:
            log.warning("rehydrate_deny reason=invalid_session")
            return None
        if wire is not None and principal.user_id != wire.user_id:
            log.warning(
                "rehydrate_deny reason=user_mismatch wire=%s store=%s",
                wire.user_id,
                principal.user_id,
            )
            return None
        return principal

    api_key = payload.get("api_key")
    if isinstance(api_key, str) and api_key.strip():
        principal = await cp.resolve_api_key(api_key.strip())
        if principal is None:
            log.warning("rehydrate_deny reason=invalid_api_key")
            return None
        if wire is not None and principal.user_id != wire.user_id:
            log.warning(
                "rehydrate_deny reason=user_mismatch wire=%s store=%s",
                wire.user_id,
                principal.user_id,
            )
            return None
        return principal

    log.warning(
        "rehydrate_deny reason=proof_required "
        "(session_token or api_key missing)"
    )
    return None
