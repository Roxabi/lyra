"""Hub identity RPC façade for thin BFF (ADR-103 Slice 2).

Dashboard process must not open ControlPlaneStore — all identity I/O goes through
``factory.dashboard.auth.*`` hub subjects via :class:`DashboardHubClient`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.core.auth.control_plane import ControlPlanePrincipal
from roxabi_contracts.dashboard import SUBJECTS
from roxabi_contracts.dashboard.auth_models import (
    DashboardAuthApiKeyResolveRequest,
    DashboardAuthInviteAcceptRequest,
    DashboardAuthInviteCreateRequest,
    DashboardAuthLoginRequest,
    DashboardAuthLogoutRequest,
    DashboardAuthPasswordChangeRequest,
    DashboardAuthPrincipal,
    DashboardAuthSessionResolveRequest,
)

if TYPE_CHECKING:
    from factory.dashboard.hub_client import DashboardHubClient

__all__ = [
    "HubAuthError",
    "auth_api_key_resolve",
    "auth_invite_accept",
    "auth_invite_create",
    "auth_login",
    "auth_logout",
    "auth_password_change",
    "auth_session_resolve",
    "principal_from_wire",
]


class HubAuthError(RuntimeError):
    """Hub identity RPC returned ok=false or an error envelope."""

    def __init__(self, error: str, message: str | None = None) -> None:
        self.error = error
        self.message = message or error
        super().__init__(self.message)


def principal_from_wire(raw: dict[str, Any] | None) -> ControlPlanePrincipal | None:
    if not raw:
        return None
    p = DashboardAuthPrincipal.model_validate(raw)
    allowed = ("session", "api_key", "platform_link", "sys", "e2e")
    via = p.via if p.via in allowed else "session"
    return ControlPlanePrincipal(
        user_id=p.user_id,
        roles=frozenset(p.roles),
        org_ids=frozenset(p.org_ids),
        active_org_id=p.active_org_id,
        via=via,  # type: ignore[arg-type]
    )


def _check(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("ok") is False or raw.get("error"):
        if raw.get("ok") is True:
            return raw
        raise HubAuthError(
            str(raw.get("error") or "error"),
            str(raw.get("message") or raw.get("error") or "auth rpc failed"),
        )
    return raw


async def auth_login(
    hub: DashboardHubClient, *, email: str, password: str
) -> dict[str, Any]:
    req = DashboardAuthLoginRequest(email=email, password=password)
    raw = await hub._request(SUBJECTS.auth_login, req.model_dump())  # noqa: SLF001
    return _check(raw)


async def auth_session_resolve(
    hub: DashboardHubClient, *, session_token: str
) -> dict[str, Any]:
    req = DashboardAuthSessionResolveRequest(session_token=session_token)
    raw = await hub._request(  # noqa: SLF001
        SUBJECTS.auth_session_resolve, req.model_dump()
    )
    return _check(raw)


async def auth_api_key_resolve(
    hub: DashboardHubClient, *, api_key: str
) -> dict[str, Any]:
    req = DashboardAuthApiKeyResolveRequest(api_key=api_key)
    raw = await hub._request(  # noqa: SLF001
        SUBJECTS.auth_api_key_resolve, req.model_dump()
    )
    return _check(raw)


async def auth_logout(
    hub: DashboardHubClient, *, session_token: str | None
) -> dict[str, Any]:
    req = DashboardAuthLogoutRequest(session_token=session_token)
    raw = await hub._request(SUBJECTS.auth_logout, req.model_dump())  # noqa: SLF001
    return _check(raw)


async def auth_invite_create(
    hub: DashboardHubClient,
    *,
    email: str,
    ttl_hours: int,
    session_token: str,
) -> dict[str, Any]:
    req = DashboardAuthInviteCreateRequest(
        email=email, ttl_hours=ttl_hours, session_token=session_token
    )
    raw = await hub._request(  # noqa: SLF001
        SUBJECTS.auth_invite_create, req.model_dump()
    )
    return _check(raw)


async def auth_invite_accept(
    hub: DashboardHubClient,
    *,
    token: str,
    password: str,
    display_name: str | None,
) -> dict[str, Any]:
    req = DashboardAuthInviteAcceptRequest(
        token=token, password=password, display_name=display_name
    )
    raw = await hub._request(  # noqa: SLF001
        SUBJECTS.auth_invite_accept, req.model_dump()
    )
    return _check(raw)


async def auth_password_change(
    hub: DashboardHubClient,
    *,
    current_password: str,
    new_password: str,
) -> dict[str, Any]:
    """Hub request; session_token stamped by hub client from request context."""
    req = DashboardAuthPasswordChangeRequest(
        current_password=current_password,
        new_password=new_password,
    )
    raw = await hub._request(  # noqa: SLF001
        SUBJECTS.auth_password_change, req.model_dump()
    )
    return _check(raw)
