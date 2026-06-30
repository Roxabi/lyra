"""Hub-side fleet list RPC for factory-dashboard BFF."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roxabi_contracts.dashboard import DashboardFleetResponse, DashboardFleetRow

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub


async def handle_fleet_list(
    hub: Hub, nc: NATS, _payload: dict[str, Any]
) -> dict[str, Any]:
    _ = nc
    store = getattr(hub, "_fleet_store", None)
    if store is None:
        return DashboardFleetResponse(rows=[]).model_dump()
    rows = [
        DashboardFleetRow(
            container_name=row.container_name,
            host=row.host,
            component_key=row.component_key,
            image_ref=row.image_ref,
            image_revision=row.image_revision,
            health=row.health,
            status=row.status,
            last_report_at=row.last_report_at,
            age_s=row.age_s,
            systemd_unit=row.systemd_unit,
            instrumented=row.instrumented,
            source=row.source,
            image_digest_status=row.image_digest_status,
        )
        for row in store.list_snapshot()
    ]
    return DashboardFleetResponse(rows=rows).model_dump()