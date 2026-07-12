"""Dashboard BFF auth — control-plane principal (ADR-103) + legacy operator token."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Cookie, Header, HTTPException, Request

from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole
from factory.core.auth.control_plane_wire import (
    ORG_HEADER,
    set_request_principal,
)
from factory.dashboard.e2e import e2e_enabled

if TYPE_CHECKING:
    from factory.core.stores.control_plane_protocol import ControlPlaneDirectory

__all__ = [
    "OperatorContext",
    "SESSION_COOKIE_NAME",
    "control_plane_from_app",
    "default_factory_tenant",
    "default_operator_user_id",
    "require_operator",
    "require_principal",
]

SESSION_COOKIE_NAME = "factory_session"


@dataclass(frozen=True, slots=True)
class OperatorContext:
    """Legacy operator principal scoped to one factory_tenant (#1992).

    Retained for connectors when control-plane store is not wired.
    Prefer :func:`require_principal` when ``app.state.control_plane`` is set.
    """

    user_id: str
    factory_tenant: str


def _configured_operator_token() -> str | None:
    raw = os.environ.get("FACTORY_DASHBOARD_OPERATOR_TOKEN", "").strip()
    return raw or None


def default_factory_tenant() -> str:
    raw = os.environ.get("FACTORY_DASHBOARD_FACTORY_TENANT", "default").strip()
    return raw or "default"


def default_operator_user_id() -> str:
    return (
        os.environ.get("FACTORY_DASHBOARD_OPERATOR_USER_ID", "operator").strip()
        or "operator"
    )


def control_plane_from_app(request: Request) -> ControlPlaneDirectory | None:
    return getattr(request.app.state, "control_plane", None)


def _admin_principal(*, user_id: str, via: str) -> ControlPlanePrincipal:
    return ControlPlanePrincipal(
        user_id=user_id,
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via=via,  # type: ignore[arg-type]
    )


def _e2e_principal() -> ControlPlanePrincipal:
    return _admin_principal(user_id="sys:e2e", via="e2e")


async def _resolve_with_control_plane(
    cp: ControlPlaneDirectory,
    *,
    authorization: str | None,
    cookie_token: str | None,
) -> ControlPlanePrincipal:
    if authorization and authorization.startswith("Bearer "):
        presented = authorization.removeprefix("Bearer ").strip()
        principal = await cp.resolve_api_key(presented)
        if principal is not None:
            return principal
        op = _configured_operator_token()
        if op and hmac.compare_digest(presented, op):
            return _admin_principal(
                user_id=default_operator_user_id(), via="api_key"
            )
        raise HTTPException(status_code=401, detail="invalid credentials")

    if cookie_token:
        principal = await cp.resolve_session(cookie_token)
        if principal is not None:
            return principal
        raise HTTPException(status_code=401, detail="session expired or invalid")

    raise HTTPException(status_code=401, detail="authentication required")


def _resolve_legacy_operator(authorization: str | None) -> ControlPlanePrincipal:
    """Shared operator token only — **fail-closed** when unset (Block 14)."""
    token = _configured_operator_token()
    if token is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required (control-plane or operator token)",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="operator auth required")
    presented = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="invalid operator token")
    return _admin_principal(user_id=default_operator_user_id(), via="api_key")


def _active_org_header(request: Request) -> str | None:
    raw = request.headers.get(ORG_HEADER) or request.headers.get(ORG_HEADER.lower())
    if raw is None:
        return None
    value = raw.strip()
    return value or None


async def require_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    factory_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> ControlPlanePrincipal:
    """Resolve session cookie or Bearer API key → principal (fail-closed).

    Rules (ADR-103 / Block 14):
    * ``FACTORY_DASHBOARD_E2E`` → synthetic admin principal.
    * Control-plane directory on ``app.state`` → require valid session or API key.
    * Directory not wired → shared operator token **required** (no Tailnet open).
    * Optional ``X-Factory-Org-Id`` must be a membership (else ignored).
    """
    if e2e_enabled():
        principal = _e2e_principal()
        set_request_principal(principal)
        return principal

    cp = control_plane_from_app(request)
    if cp is not None:
        cookie_token = factory_session or request.cookies.get(SESSION_COOKIE_NAME)
        principal = await _resolve_with_control_plane(
            cp, authorization=authorization, cookie_token=cookie_token
        )
        active = _active_org_header(request)
        if active is not None:
            if active not in principal.org_ids:
                raise HTTPException(
                    status_code=403,
                    detail="X-Factory-Org-Id is not a membership of this principal",
                )
            principal = ControlPlanePrincipal(
                user_id=principal.user_id,
                roles=principal.roles,
                org_ids=principal.org_ids,
                active_org_id=active,
                via=principal.via,
            )
        set_request_principal(principal)
        return principal
    principal = _resolve_legacy_operator(authorization)
    set_request_principal(principal)
    return principal


def require_operator(
    authorization: str | None = Header(default=None),
) -> OperatorContext:
    """Bearer operator token for connectors — fail-closed when unset (Block 14).

    Also stamps a control-plane admin principal so hub RPC wrap accepts the
    request (connectors were unstamped after ADR-103 principal gate).
    Prefer :func:`require_principal` when control-plane session/API-key is used.
    """
    from factory.dashboard.e2e import e2e_enabled as _e2e

    tenant = default_factory_tenant()
    user_id = default_operator_user_id()
    if _e2e():
        principal = _admin_principal(user_id=user_id, via="e2e")
        set_request_principal(principal)
        return OperatorContext(user_id=user_id, factory_tenant=tenant)
    token = _configured_operator_token()
    if token is None:
        raise HTTPException(
            status_code=401,
            detail="operator auth required (set FACTORY_DASHBOARD_OPERATOR_TOKEN "
            "or use session/API-key principal)",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="operator auth required")
    presented = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="invalid operator token")
    principal = _admin_principal(user_id=user_id, via="api_key")
    set_request_principal(principal)
    return OperatorContext(user_id=user_id, factory_tenant=tenant)
