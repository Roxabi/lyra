"""Tests locking event/metric subject strings and helpers."""

import pytest

from roxabi_contracts.event import SUBJECTS
from roxabi_contracts.event.subjects import per_service_event, per_service_metric


def test_event_all_subject() -> None:
    assert SUBJECTS.event_all == "factory.event.>"


def test_metric_all_subject() -> None:
    assert SUBJECTS.metric_all == "factory.metric.>"


def test_per_service_event() -> None:
    assert per_service_event("hub", "startup") == "factory.event.hub.startup"


def test_per_service_event_nested_kind() -> None:
    assert (
        per_service_event("llm", "lifecycle.swap") == "factory.event.llm.lifecycle.swap"
    )


def test_per_service_metric() -> None:
    assert (
        per_service_metric("hub", "request.count") == "factory.metric.hub.request.count"
    )


def test_per_service_event_rejects_dot_in_service() -> None:
    with pytest.raises(ValueError, match="NATS subject segment must match"):
        per_service_event("hub.prod", "startup")


def test_per_service_event_rejects_wildcard() -> None:
    with pytest.raises(ValueError, match="NATS subject segment must match"):
        per_service_event("hub*", "startup")


def test_per_service_metric_rejects_wildcard_in_name() -> None:
    with pytest.raises(ValueError, match="token"):
        per_service_metric("hub", "request*count")


def test_per_service_metric_rejects_consecutive_dots() -> None:
    with pytest.raises(ValueError, match="token"):
        per_service_metric("hub", "request..count")


def test_per_service_metric_rejects_empty() -> None:
    with pytest.raises(ValueError, match="token"):
        per_service_metric("hub", "")


_UNSAFE_SEGMENT_IDS = ["hub.prod", "hub*", "hub>", "hub space", ""]


@pytest.mark.parametrize("bad_id", _UNSAFE_SEGMENT_IDS)
def test_segment_charset_rejection(bad_id: str) -> None:
    with pytest.raises(ValueError, match="NATS subject segment must match"):
        per_service_event(bad_id, "startup")
    with pytest.raises(ValueError, match="NATS subject segment must match"):
        per_service_metric(bad_id, "request.count")
