"""Principal wire stamp for dashboard RPC payloads (ADR-103 / ADR-049 mixin)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from factory.core.auth.control_plane import ControlPlanePrincipal

__all__ = [
    "ORG_HEADER",
    "clear_request_principal",
    "get_request_principal",
    "parse_principal_from_payload",
    "set_request_principal",
    "stamp_principal_payload",
    "strip_principal_payload",
]

ORG_HEADER = "X-Factory-Org-Id"

_request_principal: ContextVar[ControlPlanePrincipal | None] = ContextVar(
    "control_plane_principal", default=None
)

_PRINCIPAL_KEYS = frozenset(
    {
        "principal_user_id",
        "principal_roles",
        "principal_org_ids",
        "principal_active_org_id",
        "principal_via",
    }
)


def set_request_principal(principal: ControlPlanePrincipal | None) -> None:
    _request_principal.set(principal)


def get_request_principal() -> ControlPlanePrincipal | None:
    return _request_principal.get()


def clear_request_principal() -> None:
    _request_principal.set(None)


def stamp_principal_payload(
    payload: dict[str, Any], principal: ControlPlanePrincipal
) -> dict[str, Any]:
    """Return a copy of *payload* with security-bearing principal fields."""
    out = dict(payload)
    out["principal_user_id"] = principal.user_id
    out["principal_roles"] = sorted(principal.roles)
    out["principal_org_ids"] = sorted(principal.org_ids)
    out["principal_active_org_id"] = principal.active_org_id
    out["principal_via"] = principal.via
    return out


def strip_principal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in _PRINCIPAL_KEYS}


def parse_principal_from_payload(
    payload: dict[str, Any],
) -> ControlPlanePrincipal | None:
    uid = payload.get("principal_user_id")
    if not uid or not isinstance(uid, str):
        return None
    roles_raw = payload.get("principal_roles") or []
    orgs_raw = payload.get("principal_org_ids") or []
    if not isinstance(roles_raw, list) or not isinstance(orgs_raw, list):
        return None
    via = payload.get("principal_via") or "session"
    if via not in ("session", "api_key", "platform_link", "sys", "e2e"):
        via = "session"
    active = payload.get("principal_active_org_id")
    if active is not None and not isinstance(active, str):
        active = None
    try:
        return ControlPlanePrincipal(
            user_id=uid,
            roles=frozenset(str(r) for r in roles_raw),
            org_ids=frozenset(str(o) for o in orgs_raw),
            active_org_id=active,
            via=via,  # type: ignore[arg-type]
        )
    except ValueError:
        return None
