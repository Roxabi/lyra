"""Tests locking event/metric subject strings and helpers."""

import pytest

from roxabi_contracts.event import SUBJECTS
from roxabi_contracts.event.subjects import per_service_event, per_service_metric


def test_event_all_subject() -> None:
    assert SUBJECTS.event_all == "lyra.event.>"


def test_metric_all_subject() -> None:
    assert SUBJECTS.metric_all == "lyra.metric.>"


def test_per_service_event() -> None:
    assert per_service_event("hub", "startup") == "lyra.event.hub.startup"


def test_per_service_event_nested_kind() -> None:
    assert per_service_event("llm", "lifecycle.swap") == "lyra.event.llm.lifecycle.swap"


def test_per_service_metric() -> None:
    assert per_service_metric("hub", "request.count") == "lyra.metric.hub.request.count"


def test_per_service_event_rejects_dot_in_service() -> None:
    with pytest.raises(ValueError, match="segment"):
        per_service_event("hub.prod", "startup")


def test_per_service_event_rejects_wildcard() -> None:
    with pytest.raises(ValueError, match="segment"):
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
