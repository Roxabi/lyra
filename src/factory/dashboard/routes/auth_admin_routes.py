"""Invite + API-key admin/member HTTP routes (thin BFF + local test path)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

from factory.core.auth.control_plane import ControlPlanePrincipal
from factory.dashboard.auth import SESSION_COOKIE_NAME, require_principal
from factory.dashboard.routes.auth_common import (
    cp_or_none,
    http_from_hub_error,
    rate_limit_or_429,
)
from factory.dashboard.routes.hub_auth import HubAuthError, auth_invite_create
from factory.dashboard.security import audit_security

__all__ = [
    "create_api_key_handler",
    "create_invite_handler",
    "list_api_keys_handler",
    "list_invites_handler",
    "revoke_api_key_handler",
    "revoke_invite_handler",
    "CreateApiKeyBody",
    "CreateInviteBody",
]


class CreateInviteBody(BaseModel):
    email: str
    ttl_hours: int = Field(default=72, ge=1, le=24 * 30)


class CreateApiKeyBody(BaseModel):
    name: str = "default"


async def create_invite_handler(
    body: CreateInviteBody,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    if not principal.is_admin:
        audit_security(
            "invite_create_denied",
            user_id=principal.user_id,
            reason="not_admin",
        )
        raise HTTPException(status_code=403, detail="admin only")
    rate_limit_or_429(request, action="invite_create")
    from factory.dashboard.auth import hub_client_from_app

    hub = hub_client_from_app(request)
    cp = cp_or_none(request)
    session_token = request.cookies.get(SESSION_COOKIE_NAME) or ""

    if hub is not None and cp is None:
        try:
            raw = await auth_invite_create(
                hub,
                email=body.email,
                ttl_hours=body.ttl_hours,
                session_token=session_token,
            )
        except HubAuthError as exc:
            raise http_from_hub_error(exc) from exc
        audit_security(
            "invite_create_ok",
            invite_id=raw.get("invite_id"),
            email=raw.get("email"),
            by=principal.user_id,
        )
        return {
            "invite": {
                "id": raw.get("invite_id"),
                "email": raw.get("email"),
                "status": raw.get("status"),
                "expires_at": raw.get("expires_at"),
                "invited_by": raw.get("invited_by"),
            },
            "token": raw.get("token"),
        }

    if cp is None:
        raise HTTPException(
            status_code=503, detail="control-plane identity not configured"
        )
    expires = datetime.now(timezone.utc) + timedelta(hours=body.ttl_hours)
    try:
        rec, token = await cp.create_invite(
            email=body.email,
            invited_by=principal.user_id,
            expires_at=expires,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_security(
        "invite_create_ok",
        invite_id=rec.id,
        email=rec.email,
        by=principal.user_id,
    )
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


async def list_invites_handler(
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    cp = cp_or_none(request)
    if cp is None:
        return {"invites": []}
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


async def revoke_invite_handler(
    invite_id: str,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    cp = cp_or_none(request)
    if cp is None:
        raise HTTPException(status_code=501, detail="invite revoke via hub not yet")
    ok = await cp.revoke_invite(invite_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail="invite not found or not pending"
        )
    return {"status": "revoked", "id": invite_id}


async def create_api_key_handler(
    body: CreateApiKeyBody,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = cp_or_none(request)
    if cp is None:
        raise HTTPException(status_code=501, detail="api key mint via hub not yet")
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


async def list_api_keys_handler(
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = cp_or_none(request)
    if cp is None:
        return {"api_keys": []}
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


async def revoke_api_key_handler(
    key_id: str,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = cp_or_none(request)
    if cp is None:
        raise HTTPException(status_code=501, detail="api key revoke via hub not yet")
    ok = await cp.revoke_api_key(key_id, user_id=principal.user_id)
    if not ok and principal.is_admin:
        ok = await cp.revoke_api_key(key_id, user_id=None)
    if not ok:
        raise HTTPException(status_code=404, detail="api key not found")
    return {"status": "revoked", "id": key_id}
