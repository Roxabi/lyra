"""FleetStore unit tests."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from factory.nats.fleet_catalog import FleetCatalogEntry
from factory.nats.fleet_store import FleetStore
from roxabi_contracts.fleet.models import new_container_report


def _catalog() -> list[FleetCatalogEntry]:
    return [
        FleetCatalogEntry(
            container_name="factory-hub",
            component_key="hub",
            image_ref="ghcr.io/roxabi/factory:staging-svc",
            systemd_unit="factory-hub.service",
            instrumented=True,
            pinned=False,
        ),
        FleetCatalogEntry(
            container_name="factory-loki",
            component_key="loki",
            image_ref="ghcr.io/roxabi/loki:staging",
            systemd_unit="factory-loki.service",
            instrumented=False,
            pinned=False,
        ),
    ]


def test_upsert_and_ok_status() -> None:
    store = FleetStore(catalog=_catalog(), report_ttl_s=90.0)
    store.upsert(
        new_container_report(
            host="roxabituwer",
            container_name="factory-hub",
            image_ref="ghcr.io/roxabi/factory:staging-svc",
            reported_at=datetime.now(UTC),
        )
    )
    rows = store.list_snapshot()
    hub = next(r for r in rows if r.container_name == "factory-hub")
    assert hub.status == "ok"
    assert hub.age_s is not None
    assert hub.age_s < 5


def test_stale_when_no_report() -> None:
    store = FleetStore(catalog=_catalog(), report_ttl_s=90.0)
    hub = next(
        r for r in store.list_snapshot() if r.container_name == "factory-hub"
    )
    assert hub.status == "stale"


def test_unknown_for_non_instrumented() -> None:
    store = FleetStore(catalog=_catalog())
    loki = next(
        r for r in store.list_snapshot() if r.container_name == "factory-loki"
    )
    assert loki.status == "unknown"
    assert loki.instrumented is False


def test_prune_drops_old_entries() -> None:
    store = FleetStore(
        catalog=_catalog(),
        report_ttl_s=1.0,
        prune_horizon_s=2.0,
    )
    store.upsert(
        new_container_report(
            host="h",
            container_name="factory-hub",
            image_ref="img:tag",
        )
    )
    store._live[("h", "factory-hub")].received_at = time.monotonic() - 5  # noqa: SLF001
    rows = store.list_snapshot()
    hub = next(r for r in rows if r.container_name == "factory-hub")
    assert hub.status == "stale"


def test_rejects_invalid_container_name() -> None:
    from roxabi_contracts.fleet.models import ContainerReport

    store = FleetStore(catalog=_catalog())
    report = ContainerReport.model_construct(
        contract_version="1",
        trace_id="t",
        issued_at=datetime.now(UTC),
        host="h",
        container_name="bad.name",
        image_ref="img",
        reported_at=datetime.now(UTC),
    )
    store.upsert(report)
    assert store._live == {}  # noqa: SLF001


def test_cap_blocks_flood() -> None:
    catalog = [
        FleetCatalogEntry(
            container_name=f"c{i}",
            component_key=f"k{i}",
            image_ref="img",
            systemd_unit=f"c{i}.service",
            instrumented=True,
            pinned=False,
        )
        for i in range(40)
    ]
    store = FleetStore(catalog=catalog)
    for i in range(40):
        store.upsert(
            new_container_report(
                host="h",
                container_name=f"c{i}",
                image_ref="img",
            )
        )
    assert len(store._live) <= 32  # noqa: SLF001