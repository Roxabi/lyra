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
    DashboardOrgCreateRequest,
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
    "org_create",
    "org_list",
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


async def _rpc(
    hub: DashboardHubClient, subject: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Identity RPC with authz errors mapped to :class:`HubAuthError`.

    ``DashboardHubClient._request`` raises ``HubUnauthorizedError`` /
    ``HubForbiddenError`` on hub ``error`` envelopes (intended for protected
    BFF routes). Public auth RPCs reuse the same error codes for login deny /
    bad session — convert so handlers can return 401/403 instead of 500.
    """
    # Lazy import: hub_client imports nothing from hub_auth (avoid cycle).
    from factory.dashboard.hub_client import HubForbiddenError, HubUnauthorizedError

    try:
        raw = await hub._request(subject, payload)  # noqa: SLF001
    except HubUnauthorizedError as exc:
        raise HubAuthError("unauthorized", str(exc)) from exc
    except HubForbiddenError as exc:
        raise HubAuthError("forbidden", str(exc)) from exc
    return _check(raw)


async def auth_login(
    hub: DashboardHubClient, *, email: str, password: str
) -> dict[str, Any]:
    req = DashboardAuthLoginRequest(email=email, password=password)
    return await _rpc(hub, SUBJECTS.auth_login, req.model_dump())


async def auth_session_resolve(
    hub: DashboardHubClient, *, session_token: str
) -> dict[str, Any]:
    req = DashboardAuthSessionResolveRequest(session_token=session_token)
    return await _rpc(hub, SUBJECTS.auth_session_resolve, req.model_dump())


async def auth_api_key_resolve(
    hub: DashboardHubClient, *, api_key: str
) -> dict[str, Any]:
    req = DashboardAuthApiKeyResolveRequest(api_key=api_key)
    return await _rpc(hub, SUBJECTS.auth_api_key_resolve, req.model_dump())


async def auth_logout(
    hub: DashboardHubClient, *, session_token: str | None
) -> dict[str, Any]:
    req = DashboardAuthLogoutRequest(session_token=session_token)
    return await _rpc(hub, SUBJECTS.auth_logout, req.model_dump())


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
    return await _rpc(hub, SUBJECTS.auth_invite_create, req.model_dump())


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
    return await _rpc(hub, SUBJECTS.auth_invite_accept, req.model_dump())


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
    return await _rpc(hub, SUBJECTS.auth_password_change, req.model_dump())


async def org_list(hub: DashboardHubClient) -> dict[str, Any]:
    return await _rpc(hub, SUBJECTS.org_list, {})


async def org_create(hub: DashboardHubClient, *, name: str) -> dict[str, Any]:
    req = DashboardOrgCreateRequest(name=name)
    return await _rpc(hub, SUBJECTS.org_create, req.model_dump())
