"""Control-plane auth HTTP routes — thin BFF over hub identity RPC (ADR-103)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from factory.core.auth.control_plane import ControlPlanePrincipal
from factory.dashboard.auth import (
    SESSION_COOKIE_NAME,
    hub_client_from_app,
    require_principal,
)
from factory.dashboard.routes.auth_admin_routes import (
    CreateApiKeyBody,
    CreateInviteBody,
    create_api_key_handler,
    create_invite_handler,
    list_api_keys_handler,
    list_invites_handler,
    revoke_api_key_handler,
    revoke_invite_handler,
)
from factory.dashboard.routes.auth_common import (
    clear_session_cookie,
    cp_or_none,
    http_from_hub_error,
    principal_public,
    rate_limit_or_429,
    set_session_cookie,
    user_public,
)
from factory.dashboard.routes.hub_auth import (
    HubAuthError,
    auth_invite_accept,
    auth_login,
    auth_logout,
    principal_from_wire,
)
from factory.dashboard.security import audit_security, client_key

__all__ = ["register_auth_routes"]


class LoginBody(BaseModel):
    email: str
    password: str


class AcceptInviteBody(BaseModel):
    token: str
    password: str = Field(min_length=8)
    display_name: str | None = None


async def _login_handler(
    body: LoginBody, request: Request, response: Response
) -> dict[str, Any]:
    rate_limit_or_429(request, action="login")
    hub = hub_client_from_app(request)
    cp = cp_or_none(request)

    if hub is not None and cp is None:
        try:
            raw = await auth_login(hub, email=body.email, password=body.password)
        except HubAuthError as exc:
            audit_security(
                "login_fail",
                email=body.email.strip().lower(),
                client=client_key(request),
            )
            raise http_from_hub_error(exc) from exc
        token = raw.get("session_token")
        if not token:
            raise HTTPException(status_code=401, detail="invalid email or password")
        ttl = int(raw.get("ttl_seconds") or 60 * 60 * 24 * 14)
        set_session_cookie(response, token, max_age=ttl)
        principal = principal_from_wire(raw.get("principal"))
        audit_security(
            "login_ok",
            user_id=(principal.user_id if principal else None),
            email=body.email.strip().lower(),
            client=client_key(request),
        )
        return {
            "user": raw.get("user") or {},
            "principal": principal_public(principal)
            if principal
            else raw.get("principal"),
        }

    if cp is None:
        raise HTTPException(
            status_code=503, detail="control-plane identity not configured"
        )
    user = await cp.verify_password(body.email, body.password)
    if user is None:
        audit_security(
            "login_fail",
            email=body.email.strip().lower(),
            client=client_key(request),
        )
        raise HTTPException(status_code=401, detail="invalid email or password")
    _session, token = await cp.create_session(user.id)
    set_session_cookie(response, token, max_age=60 * 60 * 24 * 14)
    principal = await cp.principal_for_user(user, via="session")
    audit_security(
        "login_ok",
        user_id=user.id,
        email=user.email,
        client=client_key(request),
    )
    return {
        "user": user_public(user),
        "principal": principal_public(principal),
    }


async def _accept_invite_handler(
    body: AcceptInviteBody, request: Request, response: Response
) -> dict[str, Any]:
    rate_limit_or_429(request, action="accept_invite")
    hub = hub_client_from_app(request)
    cp = cp_or_none(request)

    if hub is not None and cp is None:
        try:
            raw = await auth_invite_accept(
                hub,
                token=body.token,
                password=body.password,
                display_name=body.display_name,
            )
        except HubAuthError as exc:
            audit_security(
                "invite_accept_fail",
                client=client_key(request),
                reason=exc.message[:80],
            )
            raise http_from_hub_error(exc) from exc
        token = raw.get("session_token")
        if token:
            ttl = int(raw.get("ttl_seconds") or 60 * 60 * 24 * 14)
            set_session_cookie(response, token, max_age=ttl)
        principal = principal_from_wire(raw.get("principal"))
        return {
            "user": raw.get("user") or {},
            "principal": principal_public(principal)
            if principal
            else raw.get("principal"),
        }

    if cp is None:
        raise HTTPException(
            status_code=503, detail="control-plane identity not configured"
        )
    try:
        user = await cp.accept_invite(
            raw_token=body.token,
            password=body.password,
            display_name=body.display_name,
        )
    except ValueError as exc:
        audit_security(
            "invite_accept_fail",
            client=client_key(request),
            reason=str(exc)[:80],
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _session, token = await cp.create_session(user.id)
    set_session_cookie(response, token, max_age=60 * 60 * 24 * 14)
    principal = await cp.principal_for_user(user, via="session")
    return {
        "user": user_public(user),
        "principal": principal_public(principal),
    }


async def _logout_handler(
    request: Request,
    response: Response,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, str]:
    del principal
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    hub = hub_client_from_app(request)
    cp = cp_or_none(request)
    if hub is not None and cp is None and raw:
        try:
            await auth_logout(hub, session_token=raw)
        except HubAuthError:
            pass
    elif cp is not None and raw:
        await cp.revoke_session(raw)
    clear_session_cookie(response)
    return {"status": "ok"}


async def _me_handler(
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = cp_or_none(request)
    user = None
    if cp is not None:
        user = await cp.get_user(principal.user_id)
    return {
        "principal": principal_public(principal),
        "user": user_public(user) if user is not None else None,
    }


def register_auth_routes(router: APIRouter) -> None:
    """Mount public + protected auth endpoints on the BFF router."""
    router.add_api_route("/auth/login", _login_handler, methods=["POST"])
    router.add_api_route(
        "/auth/accept-invite", _accept_invite_handler, methods=["POST"]
    )
    router.add_api_route("/auth/logout", _logout_handler, methods=["POST"])
    router.add_api_route("/auth/me", _me_handler, methods=["GET"])
    router.add_api_route("/auth/invites", create_invite_handler, methods=["POST"])
    router.add_api_route("/auth/invites", list_invites_handler, methods=["GET"])
    router.add_api_route(
        "/auth/invites/{invite_id}/revoke",
        revoke_invite_handler,
        methods=["POST"],
    )
    router.add_api_route("/auth/api-keys", create_api_key_handler, methods=["POST"])
    router.add_api_route("/auth/api-keys", list_api_keys_handler, methods=["GET"])
    router.add_api_route(
        "/auth/api-keys/{key_id}/revoke",
        revoke_api_key_handler,
        methods=["POST"],
    )
    # Keep body models imported so OpenAPI / type checkers see them.
    _ = (CreateInviteBody, CreateApiKeyBody)
