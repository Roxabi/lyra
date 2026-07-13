"""Shared helpers for control-plane auth HTTP routes."""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request, Response

from factory.core.auth.control_plane import ControlPlanePrincipal
from factory.dashboard.auth import SESSION_COOKIE_NAME, control_plane_from_app
from factory.dashboard.routes.hub_auth import HubAuthError
from factory.dashboard.security import audit_security, check_rate_limit, client_key

__all__ = [
    "cp_or_none",
    "clear_session_cookie",
    "http_from_hub_error",
    "principal_public",
    "rate_limit_or_429",
    "set_session_cookie",
    "user_public",
]


def cp_or_none(request: Request):
    return control_plane_from_app(request)


def user_public(user: Any) -> dict[str, Any]:
    if user is None:
        return {}
    if isinstance(user, dict):
        return {
            "id": user.get("id"),
            "email": user.get("email"),
            "display_name": user.get("display_name"),
            "global_role": user.get("global_role"),
            "status": user.get("status"),
        }
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "global_role": user.global_role.value
        if hasattr(user.global_role, "value")
        else user.global_role,
        "status": user.status.value if hasattr(user.status, "value") else user.status,
    }


def principal_public(p: ControlPlanePrincipal) -> dict[str, Any]:
    return {
        "user_id": p.user_id,
        "roles": sorted(p.roles),
        "org_ids": sorted(p.org_ids),
        "active_org_id": p.active_org_id,
        "via": p.via,
    }


def cookie_secure() -> bool:
    raw = os.environ.get("FACTORY_DASHBOARD_COOKIE_INSECURE", "").strip().lower()
    return raw not in {"1", "true", "yes"}


def set_session_cookie(response: Response, token: str, *, max_age: int) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=cookie_secure(),
        httponly=True,
        samesite="lax",
    )


def rate_limit_or_429(request: Request, *, action: str) -> None:
    key = client_key(request, suffix=action)
    if not check_rate_limit(key, limit=20, window_s=60):
        audit_security("rate_limited", action=action, client=key)
        raise HTTPException(status_code=429, detail="too many attempts; try later")


def http_from_hub_error(exc: HubAuthError) -> HTTPException:
    if exc.error == "unavailable":
        code = 503
    elif exc.error == "unauthorized":
        code = 401
    elif exc.error == "forbidden":
        code = 403
    else:
        code = 400
    return HTTPException(status_code=code, detail=exc.message)
