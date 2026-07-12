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
    token = _configured_operator_token()
    if token is None:
        return _admin_principal(user_id=default_operator_user_id(), via="sys")
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
    """Resolve session cookie or Bearer API key → principal (fail-closed when CP wired).

    Rules (ADR-103):
    * ``FACTORY_DASHBOARD_E2E`` → synthetic admin principal.
    * Control-plane directory on ``app.state`` → require valid session or API key.
    * Directory not wired → fall back to legacy operator token behaviour
      (fail-open when token unset) so existing tests keep working until
      production always injects the directory.
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
        if active and active in principal.org_ids:
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
    """Legacy: validate bearer token when configured; else allow Tailnet-only.

    Prefer :func:`require_principal` for new routes. Kept for connectors until
    they migrate fully to ControlPlanePrincipal.
    """
    token = _configured_operator_token()
    tenant = default_factory_tenant()
    user_id = default_operator_user_id()
    if token is None:
        return OperatorContext(user_id=user_id, factory_tenant=tenant)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="operator auth required")
    presented = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="invalid operator token")
    return OperatorContext(user_id=user_id, factory_tenant=tenant)
