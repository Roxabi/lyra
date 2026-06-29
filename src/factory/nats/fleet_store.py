"""In-memory fleet registry — separate from WorkerRegistry (routing only)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC
from typing import Literal

from factory.nats.fleet_catalog import FleetCatalogEntry, load_fleet_catalog
from roxabi_contracts.fleet.models import ContainerReport
from roxabi_satellite.tokens import validate_nats_single_token

log = logging.getLogger(__name__)

FleetLiveStatus = Literal["ok", "stale", "unknown", "pinned"]

DEFAULT_REPORT_TTL_S = 90.0
DEFAULT_PRUNE_HORIZON_S = 180.0
MAX_ENTRIES = 32


@dataclass(slots=True)
class FleetLiveEntry:
    report: ContainerReport
    received_at: float


@dataclass(slots=True)
class FleetSnapshotRow:
    container_name: str
    host: str
    component_key: str
    image_ref: str
    image_revision: str | None
    health: str
    status: FleetLiveStatus
    last_report_at: str | None
    age_s: float | None
    systemd_unit: str
    instrumented: bool
    source: str


class FleetStore:
    def __init__(
        self,
        *,
        report_ttl_s: float = DEFAULT_REPORT_TTL_S,
        prune_horizon_s: float = DEFAULT_PRUNE_HORIZON_S,
        catalog: list[FleetCatalogEntry] | None = None,
    ) -> None:
        self._live: dict[tuple[str, str], FleetLiveEntry] = {}
        self._report_ttl_s = report_ttl_s
        self._prune_horizon_s = prune_horizon_s
        self._catalog = catalog if catalog is not None else load_fleet_catalog()
        self._rejected_ids: set[str] = set()

    def upsert(self, report: ContainerReport) -> None:
        try:
            validate_nats_single_token(report.container_name, kind="container_name")
        except ValueError:
            if report.container_name not in self._rejected_ids:
                self._rejected_ids.add(report.container_name)
                log.warning(
                    "fleet_store: rejecting invalid container_name=%r",
                    report.container_name,
                )
            return
        key = (report.host, report.container_name)
        if key not in self._live and len(self._live) >= MAX_ENTRIES:
            if report.container_name not in self._rejected_ids:
                self._rejected_ids.add(report.container_name)
                log.warning(
                    "fleet_store: registry full (%d); dropping %r",
                    MAX_ENTRIES,
                    report.container_name,
                )
            return
        self._live[key] = FleetLiveEntry(report=report, received_at=time.monotonic())

    def _prune(self) -> None:
        now = time.monotonic()
        self._live = {
            key: entry
            for key, entry in self._live.items()
            if now - entry.received_at <= self._prune_horizon_s
        }

    def _age_s(self, entry: FleetLiveEntry | None) -> float | None:
        if entry is None:
            return None
        return round(time.monotonic() - entry.received_at, 1)

    def _live_status(
        self, catalog: FleetCatalogEntry, live: FleetLiveEntry | None
    ) -> FleetLiveStatus:
        if catalog.pinned:
            return "pinned"
        if not catalog.instrumented:
            return "unknown"
        age = self._age_s(live)
        if live is None or age is None or age >= self._report_ttl_s:
            return "stale"
        return "ok"

    def list_snapshot(self) -> list[FleetSnapshotRow]:
        self._prune()
        live_by_name: dict[str, FleetLiveEntry] = {}
        for (_host, name), entry in self._live.items():
            prev = live_by_name.get(name)
            if prev is None or entry.received_at > prev.received_at:
                live_by_name[name] = entry
        rows: list[FleetSnapshotRow] = []
        for catalog in self._catalog:
            live = live_by_name.get(catalog.container_name)
            age = self._age_s(live)
            status = self._live_status(catalog, live)
            report = live.report if live else None
            rows.append(
                FleetSnapshotRow(
                    container_name=catalog.container_name,
                    host=report.host if report else "",
                    component_key=catalog.component_key,
                    image_ref=report.image_ref if report else catalog.image_ref,
                    image_revision=report.image_revision if report else None,
                    health=report.health if report else "unknown",
                    status=status,
                    last_report_at=(
                        report.reported_at.astimezone(UTC).isoformat()
                        if report
                        else None
                    ),
                    age_s=age,
                    systemd_unit=catalog.systemd_unit,
                    instrumented=catalog.instrumented,
                    source="live" if live else catalog.source,
                )
            )
        return rows

    def status_for(self, container_name: str) -> FleetLiveStatus:
        for row in self.list_snapshot():
            if row.container_name == container_name:
                return row.status
        return "unknown"