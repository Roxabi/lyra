"""Fleet ingest integration — publish ContainerReport → FleetStore via subscriber."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from nats.aio.client import Client as NATS

from factory.bootstrap.factory.dashboard_rpc import start_dashboard_rpc
from factory.bootstrap.fleet_ingest import start_fleet_ingest
from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole
from factory.core.auth.control_plane_wire import stamp_principal_payload
from factory.core.hub.hub import Hub
from factory.nats.fleet_catalog import FleetCatalogEntry
from factory.nats.fleet_store import FleetStore
from roxabi_contracts.dashboard import SUBJECTS
from roxabi_contracts.fleet import CONTAINER_REPORT
from roxabi_contracts.fleet.models import new_container_report
from roxabi_nats._serialize import serialize
from tests.nats.conftest import requires_nats_server


def _fleet_rpc_payload() -> bytes:
    """Dashboard fleet RPC requires a stamped principal (ADR-103)."""
    principal = ControlPlanePrincipal(
        user_id="rx:test-admin",
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="session",
    )
    return json.dumps(stamp_principal_payload({}, principal)).encode()


@pytest.fixture
def fleet_catalog() -> list[FleetCatalogEntry]:
    return [
        FleetCatalogEntry(
            container_name="factory-hub",
            component_key="hub",
            image_ref="ghcr.io/roxabi/factory:staging-svc",
            systemd_unit="factory-hub.service",
            instrumented=True,
            pinned=False,
        )
    ]


@requires_nats_server
@pytest.mark.asyncio
async def test_fleet_ingest_subscriber_upserts_report(
    nc: NATS, fleet_catalog: list[FleetCatalogEntry]
) -> None:
    hub = Hub()
    subs = await start_fleet_ingest(hub, nc)
    store = hub._fleet_store  # noqa: SLF001
    assert isinstance(store, FleetStore)
    store._catalog = fleet_catalog  # noqa: SLF001 — test fixture catalog

    report = new_container_report(
        host="test-host",
        container_name="factory-hub",
        image_ref="ghcr.io/roxabi/factory:staging-svc",
        reported_at=datetime.now(UTC),
    )
    await nc.publish(CONTAINER_REPORT, serialize(report))

    for _ in range(20):
        await asyncio.sleep(0.05)  # NATS delivery window
        rows = store.list_snapshot()
        hub_row = next(
            (r for r in rows if r.container_name == "factory-hub"), None
        )
        if hub_row is not None and hub_row.status == "ok":
            break
    else:
        pytest.fail("fleet ingest did not upsert factory-hub within timeout")

    hub_row = next(
        r for r in store.list_snapshot() if r.container_name == "factory-hub"
    )
    assert hub_row.status == "ok"
    assert hub_row.host == "test-host"
    assert hub_row.health == "healthy"

    for sub in subs:
        await sub.unsubscribe()


@requires_nats_server
@pytest.mark.asyncio
async def test_fleet_list_rpc_request_reply(
    nc: NATS, fleet_catalog: list[FleetCatalogEntry]
) -> None:
    hub = Hub()
    store = FleetStore(catalog=fleet_catalog)
    hub._fleet_store = store  # noqa: SLF001
    store.upsert(
        new_container_report(
            host="rpc-host",
            container_name="factory-hub",
            image_ref="ghcr.io/roxabi/factory:staging-svc",
            reported_at=datetime.now(UTC),
        )
    )

    subs = await start_dashboard_rpc(hub, nc)
    try:
        msg = await nc.request(SUBJECTS.fleet_list, _fleet_rpc_payload(), timeout=2)
        data = json.loads(msg.data.decode())
        assert "rows" in data
        hub_row = next(
            r for r in data["rows"] if r["container_name"] == "factory-hub"
        )
        assert hub_row["status"] == "ok"
        assert hub_row["host"] == "rpc-host"
    finally:
        for sub in subs:
            await sub.unsubscribe()


@requires_nats_server
@pytest.mark.asyncio
async def test_fleet_ingest_then_rpc_list(
    nc: NATS, fleet_catalog: list[FleetCatalogEntry]
) -> None:
    """Publish → ingest subscriber → fleet_list RPC without direct store seeding."""
    hub = Hub()
    ingest_subs = await start_fleet_ingest(hub, nc)
    store = hub._fleet_store  # noqa: SLF001
    assert isinstance(store, FleetStore)
    store._catalog = fleet_catalog  # noqa: SLF001

    rpc_subs = await start_dashboard_rpc(hub, nc)
    report = new_container_report(
        host="ingest-rpc-host",
        container_name="factory-hub",
        image_ref="ghcr.io/roxabi/factory:staging-svc",
        reported_at=datetime.now(UTC),
    )
    await nc.publish(CONTAINER_REPORT, serialize(report))

    hub_row = None
    for _ in range(20):
        await asyncio.sleep(0.05)  # NATS delivery window
        msg = await nc.request(SUBJECTS.fleet_list, _fleet_rpc_payload(), timeout=2)
        data = json.loads(msg.data.decode())
        hub_row = next(
            (r for r in data["rows"] if r["container_name"] == "factory-hub"),
            None,
        )
        if hub_row is not None and hub_row["status"] == "ok":
            break
    else:
        pytest.fail("ingest→RPC path did not surface factory-hub OK within timeout")

    assert hub_row is not None
    assert hub_row["host"] == "ingest-rpc-host"

    for sub in ingest_subs + rpc_subs:
        await sub.unsubscribe()
