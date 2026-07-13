"""Hub principal rehydrate — ignore client roles (ADR-103 Slice 3)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from factory.core.auth.control_plane import ControlPlanePrincipal, UserStatus

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

    Proof preference:
    1. ``session_token`` in payload → resolve_session (full rehydrate)
    2. else ``principal_user_id`` → load user + principal_for_user (roles from DB)

    Fail-closed when control plane missing or user inactive / not found.
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

    if wire is None or not wire.user_id:
        return None

    user = await cp.get_user(wire.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        log.warning(
            "rehydrate_deny reason=user_missing_or_inactive id=%s",
            wire.user_id,
        )
        return None
    # Roles/orgs always from store — ignore wire.roles / wire.org_ids.
    allowed = ("session", "api_key", "platform_link", "sys", "e2e")
    via = wire.via if wire.via in allowed else "session"
    return await cp.principal_for_user(user, via=via)
