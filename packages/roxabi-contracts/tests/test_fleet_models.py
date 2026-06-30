"""Fleet contract round-trip + subject literal tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from roxabi_contracts.fleet import CONTAINER_REPORT, SUBJECTS, ContainerReport
from roxabi_contracts.fleet.models import new_container_report


def test_container_report_subject_literal() -> None:
    assert CONTAINER_REPORT == "factory.metric.host.container_report"
    assert SUBJECTS.container_report == "factory.metric.host.container_report"


def test_container_report_roundtrip() -> None:
    report = new_container_report(
        host="roxabituwer",
        container_name="factory-hub",
        image_ref="ghcr.io/roxabi/factory:staging-svc",
        image_revision="abc123",
        health="healthy",
    )
    restored = ContainerReport.model_validate_json(report.model_dump_json())
    assert restored.container_name == "factory-hub"
    assert restored.image_ref.endswith("staging-svc")
    assert restored.image_revision == "abc123"
    assert restored.health == "healthy"
    assert restored.systemd_unit == "factory-hub.service"


def test_container_report_rejects_invalid_name() -> None:
    with pytest.raises(ValidationError):
        new_container_report(
            host="h",
            container_name="bad.name",
            image_ref="img:tag",
        )


def test_container_report_extra_fields_ignored() -> None:
    raw = new_container_report(
        host="h",
        container_name="factory-loki",
        image_ref="ghcr.io/roxabi/loki:staging",
    ).model_dump()
    raw["future_field"] = "ignored"
    parsed = ContainerReport.model_validate(raw)
    assert parsed.container_name == "factory-loki"


def test_reported_at_preserved() -> None:
    ts = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    report = new_container_report(
        host="h",
        container_name="factory-nats",
        image_ref="nats:2",
        reported_at=ts,
    )
    assert report.reported_at == ts