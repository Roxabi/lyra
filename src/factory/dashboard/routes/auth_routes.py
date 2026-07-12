"""Control-plane auth HTTP routes — login, invite, session, API keys (ADR-103)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from factory.core.auth.control_plane import ControlPlanePrincipal
from factory.dashboard.auth import (
    SESSION_COOKIE_NAME,
    control_plane_from_app,
    require_principal,
)

__all__ = ["register_auth_routes"]


class LoginBody(BaseModel):
    email: str
    password: str


class AcceptInviteBody(BaseModel):
    token: str
    password: str = Field(min_length=8)
    display_name: str | None = None


class CreateInviteBody(BaseModel):
    email: str
    ttl_hours: int = Field(default=72, ge=1, le=24 * 30)


class CreateApiKeyBody(BaseModel):
    name: str = "default"


def _cp_or_503(request: Request):
    cp = control_plane_from_app(request)
    if cp is None:
        raise HTTPException(
            status_code=503,
            detail="control-plane identity store not configured",
        )
    return cp


def _user_public(user: Any) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "global_role": user.global_role.value
        if hasattr(user.global_role, "value")
        else user.global_role,
        "status": user.status.value if hasattr(user.status, "value") else user.status,
    }


def _principal_public(p: ControlPlanePrincipal) -> dict[str, Any]:
    return {
        "user_id": p.user_id,
        "roles": sorted(p.roles),
        "org_ids": sorted(p.org_ids),
        "active_org_id": p.active_org_id,
        "via": p.via,
    }


def _cookie_secure() -> bool:
    """Secure cookies by default; FACTORY_DASHBOARD_COOKIE_INSECURE=1 for HTTP."""
    raw = os.environ.get("FACTORY_DASHBOARD_COOKIE_INSECURE", "").strip().lower()
    return raw not in {"1", "true", "yes"}


def _set_session_cookie(response: Response, token: str, *, max_age: int) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=max_age,
        path="/",
    )


async def _login_handler(
    body: LoginBody, request: Request, response: Response
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    user = await cp.verify_password(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    _session, token = await cp.create_session(user.id)
    _set_session_cookie(response, token, max_age=60 * 60 * 24 * 14)
    principal = await cp.principal_for_user(user, via="session")
    return {
        "user": _user_public(user),
        "principal": _principal_public(principal),
    }


async def _accept_invite_handler(
    body: AcceptInviteBody, request: Request, response: Response
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    try:
        user = await cp.accept_invite(
            raw_token=body.token,
            password=body.password,
            display_name=body.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _session, token = await cp.create_session(user.id)
    _set_session_cookie(response, token, max_age=60 * 60 * 24 * 14)
    principal = await cp.principal_for_user(user, via="session")
    return {
        "user": _user_public(user),
        "principal": _principal_public(principal),
    }


async def _logout_handler(
    request: Request,
    response: Response,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, str]:
    del principal
    cp = control_plane_from_app(request)
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if cp is not None and raw:
        await cp.revoke_session(raw)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


async def _me_handler(
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = control_plane_from_app(request)
    user = await cp.get_user(principal.user_id) if cp is not None else None
    return {
        "principal": _principal_public(principal),
        "user": _user_public(user) if user is not None else None,
    }


async def _create_invite_handler(
    body: CreateInviteBody,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    cp = _cp_or_503(request)
    expires = datetime.now(timezone.utc) + timedelta(hours=body.ttl_hours)
    try:
        rec, token = await cp.create_invite(
            email=body.email,
            invited_by=principal.user_id,
            expires_at=expires,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "invite": {
            "id": rec.id,
            "email": rec.email,
            "status": rec.status.value,
            "expires_at": rec.expires_at.isoformat(),
            "invited_by": rec.invited_by,
        },
        "token": token,
    }


async def _list_invites_handler(
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    cp = _cp_or_503(request)
    rows = await cp.list_invites()
    return {
        "invites": [
            {
                "id": r.id,
                "email": r.email,
                "status": r.status.value,
                "expires_at": r.expires_at.isoformat(),
                "invited_by": r.invited_by,
            }
            for r in rows
        ]
    }


async def _revoke_invite_handler(
    invite_id: str,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    cp = _cp_or_503(request)
    ok = await cp.revoke_invite(invite_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail="invite not found or not pending"
        )
    return {"status": "revoked", "id": invite_id}


async def _create_api_key_handler(
    body: CreateApiKeyBody,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    rec, secret = await cp.create_api_key(principal.user_id, name=body.name)
    return {
        "api_key": {
            "id": rec.id,
            "name": rec.name,
            "prefix": rec.prefix,
            "created_at": rec.created_at.isoformat(),
        },
        "secret": secret,
    }


async def _list_api_keys_handler(
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    rows = await cp.list_api_keys(principal.user_id)
    return {
        "api_keys": [
            {
                "id": r.id,
                "name": r.name,
                "prefix": r.prefix,
                "created_at": r.created_at.isoformat(),
                "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
            }
            for r in rows
        ]
    }


async def _revoke_api_key_handler(
    key_id: str,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    ok = await cp.revoke_api_key(key_id, user_id=principal.user_id)
    if not ok and principal.is_admin:
        ok = await cp.revoke_api_key(key_id, user_id=None)
    if not ok:
        raise HTTPException(status_code=404, detail="api key not found")
    return {"status": "revoked", "id": key_id}


def register_auth_routes(router: APIRouter) -> None:
    """Mount public + protected auth endpoints on the BFF router."""
    router.add_api_route("/auth/login", _login_handler, methods=["POST"])
    router.add_api_route(
        "/auth/accept-invite", _accept_invite_handler, methods=["POST"]
    )
    router.add_api_route("/auth/logout", _logout_handler, methods=["POST"])
    router.add_api_route("/auth/me", _me_handler, methods=["GET"])
    router.add_api_route("/auth/invites", _create_invite_handler, methods=["POST"])
    router.add_api_route("/auth/invites", _list_invites_handler, methods=["GET"])
    router.add_api_route(
        "/auth/invites/{invite_id}/revoke",
        _revoke_invite_handler,
        methods=["POST"],
    )
    router.add_api_route(
        "/auth/api-keys", _create_api_key_handler, methods=["POST"]
    )
    router.add_api_route("/auth/api-keys", _list_api_keys_handler, methods=["GET"])
    router.add_api_route(
        "/auth/api-keys/{key_id}",
        _revoke_api_key_handler,
        methods=["DELETE"],
    )
