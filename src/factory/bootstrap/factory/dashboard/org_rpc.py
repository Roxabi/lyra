"""Hub org directory RPC — factory.dashboard.org.* (thin BFF, hub sole IdP)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from factory.core.auth.control_plane_authz import authorize
from factory.core.auth.control_plane_wire import get_request_principal
from roxabi_contracts.dashboard.auth_models import (
    DashboardOrgCreateRequest,
    DashboardOrgCreateResponse,
    DashboardOrgListResponse,
    DashboardOrgRow,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub
    from factory.core.stores.control_plane_protocol import ControlPlaneDirectory

log = logging.getLogger(__name__)


def _cp(hub: Hub) -> ControlPlaneDirectory | None:
    return getattr(hub, "_control_plane", None)


def _org_row(org: Any) -> DashboardOrgRow:
    return DashboardOrgRow(
        id=org.id,
        name=org.name,
        created_by=org.created_by,
        created_at=org.created_at.isoformat(),
    )


async def handle_org_list(
    hub: Hub, _nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    """List orgs for the rehydrated principal (self memberships)."""
    del payload
    cp = _cp(hub)
    if cp is None:
        return DashboardOrgListResponse(
            ok=False, error="unavailable", message="control-plane not configured"
        ).model_dump()
    principal = get_request_principal()
    if principal is None:
        return DashboardOrgListResponse(
            ok=False, error="unauthorized", message="principal required"
        ).model_dump()
    orgs = await cp.list_orgs_for_user(principal.user_id)
    return DashboardOrgListResponse(
        ok=True,
        orgs=[_org_row(o) for o in orgs],
        active_org_id=principal.active_org_id,
    ).model_dump()


async def handle_org_create(
    hub: Hub, _nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    """Create org as rehydrated principal (authz orgs.create)."""
    cp = _cp(hub)
    if cp is None:
        return DashboardOrgCreateResponse(
            ok=False, error="unavailable", message="control-plane not configured"
        ).model_dump()
    principal = get_request_principal()
    if principal is None:
        return DashboardOrgCreateResponse(
            ok=False, error="unauthorized", message="principal required"
        ).model_dump()
    decision = authorize(principal, "orgs.create")
    if not decision.allowed:
        return DashboardOrgCreateResponse(
            ok=False, error="forbidden", message=decision.reason or "forbidden"
        ).model_dump()
    req = DashboardOrgCreateRequest.model_validate(payload)
    try:
        org = await cp.create_org(name=req.name, created_by=principal.user_id)
    except ValueError as exc:
        log.info("org_rpc create_fail reason=%s", str(exc)[:80])
        return DashboardOrgCreateResponse(
            ok=False, error="bad_request", message=str(exc)[:120]
        ).model_dump()
    log.info("org_rpc create_ok org_id=%s user_id=%s", org.id, principal.user_id)
    return DashboardOrgCreateResponse(ok=True, org=_org_row(org)).model_dump()
