"""Hub-side dashboard connector installation RPC handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.bootstrap.factory.ingress_registry_service import get_installation_store
from roxabi_contracts.dashboard import (
    ConnectorInstallationRow,
    DashboardConnectorInstallationDeleteRequest,
    DashboardConnectorInstallationDeleteResponse,
    DashboardConnectorInstallationsListRequest,
    DashboardConnectorInstallationsListResponse,
    DashboardConnectorInstallationUpsertRequest,
    DashboardConnectorInstallationUpsertResponse,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

_SUPPORTED_CONNECTORS = frozenset({"github", "cloudflare"})


def _tenant_mismatch_response() -> dict[str, Any]:
    return {"error": "tenant_forbidden"}


async def handle_connectors_list(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = hub, nc
    req = DashboardConnectorInstallationsListRequest.model_validate(payload)
    if req.connector not in _SUPPORTED_CONNECTORS:
        return DashboardConnectorInstallationsListResponse(
            installations=[]
        ).model_dump()
    store = await get_installation_store()
    rows = await store.list_rows()
    installations = [
        ConnectorInstallationRow(
            connector=str(row["connector"]),
            external_id=str(row["external_id"]),
            factory_tenant=str(row["factory_tenant"]),
            enabled=bool(row["enabled"]),
        )
        for row in rows
        if row["connector"] == req.connector
        and row["factory_tenant"] == req.factory_tenant
    ]
    return DashboardConnectorInstallationsListResponse(
        installations=installations
    ).model_dump()


async def handle_connectors_upsert(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = hub, nc
    req = DashboardConnectorInstallationUpsertRequest.model_validate(payload)
    if req.connector not in _SUPPORTED_CONNECTORS:
        return {"error": "unknown_connector"}
    if req.factory_tenant != req.operator_tenant:
        return _tenant_mismatch_response()
    store = await get_installation_store()
    await store.upsert_lifecycle(
        req.connector,
        req.external_id,
        req.factory_tenant,
        enabled=True,
    )
    return DashboardConnectorInstallationUpsertResponse().model_dump()


async def handle_connectors_delete(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = hub, nc
    req = DashboardConnectorInstallationDeleteRequest.model_validate(payload)
    if req.connector not in _SUPPORTED_CONNECTORS:
        return {"error": "unknown_connector"}
    store = await get_installation_store()
    rows = await store.list_rows()
    match = next(
        (
            row
            for row in rows
            if row["connector"] == req.connector
            and row["external_id"] == req.external_id
        ),
        None,
    )
    if match is None:
        return DashboardConnectorInstallationDeleteResponse().model_dump()
    if str(match["factory_tenant"]) != req.operator_tenant:
        return _tenant_mismatch_response()
    await store.upsert_lifecycle(
        req.connector,
        req.external_id,
        str(match["factory_tenant"]),
        enabled=False,
    )
    return DashboardConnectorInstallationDeleteResponse().model_dump()
