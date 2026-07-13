"""Dashboard hub identity RPC wire models (ADR-103 hub sole IdP / Slice 1).

Subjects live under ``factory.dashboard.auth.*``. Edge cookie carries opaque
session token only; roles always rehydrated on hub (Slice 3 fail-closed).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AuthViaWire = Literal["session", "api_key", "platform_link", "sys", "e2e"]


class DashboardAuthUser(BaseModel):
    id: str
    email: str | None = None
    display_name: str | None = None
    global_role: str
    status: str


class DashboardAuthPrincipal(BaseModel):
    """Resolved principal (roles rehydrated from store — never edge-trusted)."""

    user_id: str
    roles: list[str] = Field(default_factory=list)
    org_ids: list[str] = Field(default_factory=list)
    active_org_id: str | None = None
    via: AuthViaWire = "session"
    session_id: str | None = None
    api_key_id: str | None = None


class DashboardAuthLoginRequest(BaseModel):
    email: str
    password: str


class DashboardAuthLoginResponse(BaseModel):
    ok: bool = True
    error: str | None = None
    message: str | None = None
    user: DashboardAuthUser | None = None
    principal: DashboardAuthPrincipal | None = None
    session_token: str | None = None
    session_id: str | None = None
    ttl_seconds: int | None = None


class DashboardAuthSessionResolveRequest(BaseModel):
    """Opaque session token from edge cookie (no roles)."""

    session_token: str


class DashboardAuthSessionResolveResponse(BaseModel):
    ok: bool = True
    error: str | None = None
    message: str | None = None
    user: DashboardAuthUser | None = None
    principal: DashboardAuthPrincipal | None = None
    session_id: str | None = None


class DashboardAuthLogoutRequest(BaseModel):
    session_token: str | None = None


class DashboardAuthLogoutResponse(BaseModel):
    ok: bool = True
    error: str | None = None
    message: str | None = None


class DashboardAuthInviteCreateRequest(BaseModel):
    email: str
    ttl_hours: int = Field(default=72, ge=1, le=24 * 30)
    # Opaque session proof — hub rehydrates principal; wire roles ignored (Slice 1).
    session_token: str | None = None


class DashboardAuthInviteCreateResponse(BaseModel):
    ok: bool = True
    error: str | None = None
    message: str | None = None
    invite_id: str | None = None
    email: str | None = None
    status: str | None = None
    expires_at: str | None = None
    invited_by: str | None = None
    token: str | None = None


class DashboardAuthInviteAcceptRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)
    display_name: str | None = None


class DashboardAuthInviteAcceptResponse(BaseModel):
    ok: bool = True
    error: str | None = None
    message: str | None = None
    user: DashboardAuthUser | None = None
    principal: DashboardAuthPrincipal | None = None
    session_token: str | None = None
    session_id: str | None = None
    ttl_seconds: int | None = None


class DashboardAuthApiKeyResolveRequest(BaseModel):
    api_key: str


class DashboardAuthApiKeyResolveResponse(BaseModel):
    ok: bool = True
    error: str | None = None
    message: str | None = None
    user: DashboardAuthUser | None = None
    principal: DashboardAuthPrincipal | None = None
    api_key_id: str | None = None
