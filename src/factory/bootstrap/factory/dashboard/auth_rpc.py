"""Hub identity lifecycle RPC — factory.dashboard.auth.* (ADR-103 Slice 1).

Uses hub ``_control_plane`` only (existing ControlPlaneStore). Public handlers
(login / accept-invite / session.resolve / api_key.resolve) do not require a
pre-stamped principal; invite.create requires admin principal.

V1: opaque server sessions; edge cookie = session token only (no roles).
RPC stamp design: user_id + session_id (or api_key id); roles rehydrated from
store (Slice 3 fail-closed on business RPCs).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from factory.core.auth.control_plane import ControlPlanePrincipal, ControlPlaneUser
from factory.core.auth.control_plane_authz import authorize
from factory.core.auth.control_plane_wire import get_request_principal
from roxabi_contracts.dashboard.auth_models import (
    DashboardAuthApiKeyResolveRequest,
    DashboardAuthApiKeyResolveResponse,
    DashboardAuthInviteAcceptRequest,
    DashboardAuthInviteAcceptResponse,
    DashboardAuthInviteCreateRequest,
    DashboardAuthInviteCreateResponse,
    DashboardAuthLoginRequest,
    DashboardAuthLoginResponse,
    DashboardAuthLogoutRequest,
    DashboardAuthLogoutResponse,
    DashboardAuthPrincipal,
    DashboardAuthSessionResolveRequest,
    DashboardAuthSessionResolveResponse,
    DashboardAuthUser,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub
    from factory.core.stores.control_plane_protocol import ControlPlaneDirectory

log = logging.getLogger(__name__)

_DEFAULT_SESSION_TTL = 1_209_600  # 14d


def _cp(hub: Hub) -> ControlPlaneDirectory | None:
    return getattr(hub, "_control_plane", None)


def _user_public(user: ControlPlaneUser) -> DashboardAuthUser:
    return DashboardAuthUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        global_role=user.global_role.value
        if hasattr(user.global_role, "value")
        else str(user.global_role),
        status=user.status.value if hasattr(user.status, "value") else str(user.status),
    )


def _principal_public(
    principal: ControlPlanePrincipal,
    *,
    session_id: str | None = None,
    api_key_id: str | None = None,
) -> DashboardAuthPrincipal:
    return DashboardAuthPrincipal(
        user_id=principal.user_id,
        roles=sorted(principal.roles),
        org_ids=sorted(principal.org_ids),
        active_org_id=principal.active_org_id,
        via=principal.via,  # type: ignore[arg-type]
        session_id=session_id,
        api_key_id=api_key_id,
    )


async def handle_auth_login(
    hub: Hub, _nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    """Public: email/password → opaque session token + rehydrated principal."""
    cp = _cp(hub)
    if cp is None:
        return DashboardAuthLoginResponse(
            ok=False, error="unavailable", message="control-plane not configured"
        ).model_dump()
    req = DashboardAuthLoginRequest.model_validate(payload)
    user = await cp.verify_password(req.email, req.password)
    if user is None:
        log.info("auth_rpc login_deny email=%s", req.email.strip().lower())
        return DashboardAuthLoginResponse(
            ok=False, error="unauthorized", message="invalid email or password"
        ).model_dump()
    session, token = await cp.create_session(user.id, ttl_seconds=_DEFAULT_SESSION_TTL)
    principal = await cp.principal_for_user(user, via="session")
    log.info("auth_rpc login_ok user_id=%s", user.id)
    return DashboardAuthLoginResponse(
        ok=True,
        user=_user_public(user),
        principal=_principal_public(principal, session_id=session.id),
        session_token=token,
        session_id=session.id,
        ttl_seconds=_DEFAULT_SESSION_TTL,
    ).model_dump()


async def handle_auth_session_resolve(
    hub: Hub, _nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    """Public: opaque session token → principal (store rehydrate)."""
    cp = _cp(hub)
    if cp is None:
        return DashboardAuthSessionResolveResponse(
            ok=False, error="unavailable", message="control-plane not configured"
        ).model_dump()
    req = DashboardAuthSessionResolveRequest.model_validate(payload)
    principal = await cp.resolve_session(req.session_token)
    if principal is None:
        return DashboardAuthSessionResolveResponse(
            ok=False, error="unauthorized", message="session expired or invalid"
        ).model_dump()
    user = await cp.get_user(principal.user_id)
    return DashboardAuthSessionResolveResponse(
        ok=True,
        user=_user_public(user) if user is not None else None,
        principal=_principal_public(principal),
        session_id=None,
    ).model_dump()


async def handle_auth_api_key_resolve(
    hub: Hub, _nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    """Public: API key secret → principal (store rehydrate)."""
    cp = _cp(hub)
    if cp is None:
        return DashboardAuthApiKeyResolveResponse(
            ok=False, error="unavailable", message="control-plane not configured"
        ).model_dump()
    req = DashboardAuthApiKeyResolveRequest.model_validate(payload)
    principal = await cp.resolve_api_key(req.api_key)
    if principal is None:
        return DashboardAuthApiKeyResolveResponse(
            ok=False, error="unauthorized", message="invalid api key"
        ).model_dump()
    user = await cp.get_user(principal.user_id)
    return DashboardAuthApiKeyResolveResponse(
        ok=True,
        user=_user_public(user) if user is not None else None,
        principal=_principal_public(principal),
    ).model_dump()


async def handle_auth_logout(
    hub: Hub, _nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    """Revoke opaque session when token presented (best-effort)."""
    cp = _cp(hub)
    if cp is None:
        return DashboardAuthLogoutResponse(
            ok=False, error="unavailable", message="control-plane not configured"
        ).model_dump()
    req = DashboardAuthLogoutRequest.model_validate(payload)
    if req.session_token:
        await cp.revoke_session(req.session_token)
    return DashboardAuthLogoutResponse(ok=True).model_dump()


async def handle_auth_invite_create(
    hub: Hub, _nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    """Admin-only invite create (principal required via wrap)."""
    cp = _cp(hub)
    if cp is None:
        return DashboardAuthInviteCreateResponse(
            ok=False, error="unavailable", message="control-plane not configured"
        ).model_dump()
    principal = get_request_principal()
    if principal is None:
        return DashboardAuthInviteCreateResponse(
            ok=False, error="unauthorized", message="principal required"
        ).model_dump()
    decision = authorize(principal, "users.invite")
    if not decision.allowed:
        log.warning(
            "auth_rpc invite_create_deny user_id=%s reason=%s",
            principal.user_id,
            decision.reason,
        )
        return DashboardAuthInviteCreateResponse(
            ok=False, error="forbidden", message="admin only"
        ).model_dump()
    req = DashboardAuthInviteCreateRequest.model_validate(payload)
    expires = datetime.now(timezone.utc) + timedelta(hours=req.ttl_hours)
    try:
        rec, token = await cp.create_invite(
            email=req.email,
            invited_by=principal.user_id,
            expires_at=expires,
        )
    except ValueError as exc:
        return DashboardAuthInviteCreateResponse(
            ok=False, error="bad_request", message=str(exc)
        ).model_dump()
    return DashboardAuthInviteCreateResponse(
        ok=True,
        invite_id=rec.id,
        email=rec.email,
        status=rec.status.value,
        expires_at=rec.expires_at.isoformat(),
        invited_by=rec.invited_by,
        token=token,
    ).model_dump()


async def handle_auth_invite_accept(
    hub: Hub, _nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    """Public: consume invite token → member + session."""
    cp = _cp(hub)
    if cp is None:
        return DashboardAuthInviteAcceptResponse(
            ok=False, error="unavailable", message="control-plane not configured"
        ).model_dump()
    req = DashboardAuthInviteAcceptRequest.model_validate(payload)
    try:
        user = await cp.accept_invite(
            raw_token=req.token,
            password=req.password,
            display_name=req.display_name,
        )
    except ValueError as exc:
        return DashboardAuthInviteAcceptResponse(
            ok=False, error="bad_request", message=str(exc)
        ).model_dump()
    session, token = await cp.create_session(user.id, ttl_seconds=_DEFAULT_SESSION_TTL)
    principal = await cp.principal_for_user(user, via="session")
    return DashboardAuthInviteAcceptResponse(
        ok=True,
        user=_user_public(user),
        principal=_principal_public(principal, session_id=session.id),
        session_token=token,
        session_id=session.id,
        ttl_seconds=_DEFAULT_SESSION_TTL,
    ).model_dump()
