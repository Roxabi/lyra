"""Fleet ingest integration — publish ContainerReport → FleetStore via subscriber."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from nats.aio.client import Client as NATS

from factory.bootstrap.factory.fleet_bootstrap import start_fleet_ingest
from factory.core.hub.hub import Hub
from factory.nats.fleet_catalog import FleetCatalogEntry
from factory.nats.fleet_store import FleetStore
from roxabi_contracts.fleet import CONTAINER_REPORT
from roxabi_contracts.fleet.models import new_container_report
from roxabi_nats._serialize import serialize
from tests.nats.conftest import requires_nats_server


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
        await asyncio.sleep(0.05)
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