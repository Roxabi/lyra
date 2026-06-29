"""Fleet container report models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import field_validator

from roxabi_contracts.envelope import CONTRACT_VERSION, ContractEnvelope

ContainerHealth = Literal["starting", "healthy", "unhealthy", "unknown"]


class ContainerReport(ContractEnvelope):
    """Periodic container liveness snapshot (plane ③)."""

    host: str
    container_name: str
    image_ref: str
    image_revision: str | None = None
    health: ContainerHealth = "healthy"
    systemd_unit: str | None = None
    reported_at: datetime

    @field_validator("container_name", mode="before")
    @classmethod
    def _validate_container_name(cls, value: str) -> str:
        from roxabi_contracts._nats_utils import validate_worker_id

        validate_worker_id(str(value))
        return str(value)


def new_container_report(  # noqa: PLR0913 — builder with optional fleet fields
    *,
    host: str,
    container_name: str,
    image_ref: str,
    image_revision: str | None = None,
    health: ContainerHealth = "healthy",
    systemd_unit: str | None = None,
    reported_at: datetime | None = None,
    trace_id: str | None = None,
) -> ContainerReport:
    """Build a stamped ContainerReport for publishers."""
    from datetime import UTC

    now = reported_at or datetime.now(UTC)
    unit = systemd_unit or f"{container_name}.service"
    return ContainerReport(
        contract_version=CONTRACT_VERSION,
        trace_id=trace_id or uuid4().hex,
        issued_at=now,
        host=host,
        container_name=container_name,
        image_ref=image_ref,
        image_revision=image_revision,
        health=health,
        systemd_unit=unit,
        reported_at=now,
    )