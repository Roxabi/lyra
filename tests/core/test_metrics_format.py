"""Unit tests — verify spec C3 log format strings for WorkerError metrics helpers."""

import logging

import roxabi_contracts
from factory.core.messaging.utils.metrics import (
    emit_populated_total,
    emit_received_total,
    log_contracts_version,
)

_METRICS_LOGGER = "factory.core.messaging.utils.metrics"


def _has(records: list[logging.LogRecord], expected: str) -> bool:
    """Return True when the metrics logger emitted a record with `expected`.

    Order-independent AND logger-scoped: `at_level(..., logger=...)` only sets
    the level threshold; pytest still captures records from every logger. We
    filter on `r.name` so a coincidentally-equal message from another logger
    can't false-pass under parallel runs.
    """
    return any(
        r.name == _METRICS_LOGGER and r.getMessage() == expected for r in records
    )


def test_emit_populated_total_format(caplog):
    with caplog.at_level(logging.INFO, logger=_METRICS_LOGGER):
        emit_populated_total("cli")
    expected = "METRIC worker_error_populated_total domain=cli count=1"
    assert _has(caplog.records, expected), [r.getMessage() for r in caplog.records]


def test_emit_received_total_format(caplog):
    with caplog.at_level(logging.INFO, logger=_METRICS_LOGGER):
        emit_received_total("transport.timeout", "llm")
    expected = (
        "METRIC worker_error_received_total code=transport.timeout domain=llm count=1"
    )
    assert _has(caplog.records, expected), [r.getMessage() for r in caplog.records]


def test_log_contracts_version_format(caplog):
    with caplog.at_level(logging.INFO, logger=_METRICS_LOGGER):
        log_contracts_version()
    expected = f"roxabi_contracts_version={roxabi_contracts.__version__}"
    assert _has(caplog.records, expected), [r.getMessage() for r in caplog.records]
