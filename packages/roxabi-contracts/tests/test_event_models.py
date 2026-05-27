"""Roundtrip + invariant tests for event/metric contract models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from roxabi_contracts.event import LyraEvent, LyraMetric

_ENV: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-trace",
    "issued_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
}


# --- LyraEvent ---


def test_event_roundtrip() -> None:
    evt = LyraEvent(
        **_ENV,
        service="hub",
        kind="startup",
        level="info",
        message="Hub started",
        payload={"pid": 1234},
    )
    restored = LyraEvent.model_validate_json(evt.model_dump_json())
    assert restored.service == "hub"
    assert restored.kind == "startup"
    assert restored.level == "info"
    assert restored.message == "Hub started"
    assert restored.payload == {"pid": 1234}


def test_event_minimal() -> None:
    evt = LyraEvent(**_ENV, service="hub", kind="heartbeat", level="debug")
    assert evt.message is None
    assert evt.payload is None


def test_event_error_level_requires_message() -> None:
    with pytest.raises(ValidationError, match="must carry a message"):
        LyraEvent(**_ENV, service="hub", kind="crash", level="error", message=None)


def test_event_critical_level_requires_message() -> None:
    with pytest.raises(ValidationError, match="must carry a message"):
        LyraEvent(**_ENV, service="hub", kind="alert", level="critical", message=None)


def test_event_warn_without_message_ok() -> None:
    evt = LyraEvent(**_ENV, service="hub", kind="degraded", level="warn")
    assert evt.message is None


def test_event_invalid_service() -> None:
    with pytest.raises(ValidationError):
        LyraEvent(**_ENV, service="Hub", kind="startup", level="info")


def test_event_invalid_kind() -> None:
    with pytest.raises(ValidationError):
        LyraEvent(**_ENV, service="hub", kind="Startup!", level="info")


def test_event_invalid_level() -> None:
    with pytest.raises(ValidationError):
        LyraEvent.model_validate(
            {
                **_ENV,
                "service": "hub",
                "kind": "startup",
                "level": "verbose",
            }
        )


def test_event_extra_fields_ignored() -> None:
    evt = LyraEvent.model_validate(
        {
            **_ENV,
            "service": "hub",
            "kind": "startup",
            "level": "info",
            "unknown_field": "x",
        }
    )
    assert not hasattr(evt, "unknown_field")


# --- LyraMetric ---


def test_metric_counter_roundtrip() -> None:
    m = LyraMetric(
        **_ENV,
        service="hub",
        name="request.count",
        metric_type="counter",
        value=1.0,
        unit="count",
        labels={"platform": "telegram"},
    )
    restored = LyraMetric.model_validate_json(m.model_dump_json())
    assert restored.service == "hub"
    assert restored.name == "request.count"
    assert restored.metric_type == "counter"
    assert restored.value == 1.0
    assert restored.unit == "count"
    assert restored.labels == {"platform": "telegram"}


def test_metric_gauge_minimal() -> None:
    m = LyraMetric(
        **_ENV, service="llm", name="queue.depth", metric_type="gauge", value=3.0
    )
    assert m.unit is None
    assert m.labels is None


def test_metric_histogram_no_labels() -> None:
    m = LyraMetric(
        **_ENV,
        service="hub",
        name="request.latency",
        metric_type="histogram",
        value=42.5,
        unit="ms",
    )
    assert m.labels is None
    assert m.value == 42.5


def test_metric_invalid_service() -> None:
    with pytest.raises(ValidationError):
        LyraMetric(
            **_ENV, service="HUB", name="count", metric_type="counter", value=1.0
        )


def test_metric_invalid_name() -> None:
    with pytest.raises(ValidationError):
        LyraMetric(
            **_ENV, service="hub", name="Count!", metric_type="counter", value=1.0
        )


def test_metric_invalid_type() -> None:
    with pytest.raises(ValidationError):
        LyraMetric.model_validate(
            {
                **_ENV,
                "service": "hub",
                "name": "count",
                "metric_type": "summary",
                "value": 1.0,
            }
        )


def test_metric_extra_fields_ignored() -> None:
    m = LyraMetric.model_validate(
        {
            **_ENV,
            "service": "hub",
            "name": "count",
            "metric_type": "counter",
            "value": 1.0,
            "unknown_field": "x",
        }
    )
    assert not hasattr(m, "unknown_field")
