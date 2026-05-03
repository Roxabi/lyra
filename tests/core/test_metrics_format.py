"""Unit tests — verify spec C3 log format strings for WorkerError metrics helpers."""

import logging

import roxabi_contracts
from lyra.core.messaging.metrics import (
    emit_populated_total,
    emit_received_total,
    log_contracts_version,
)

_METRICS_LOGGER = "lyra.core.messaging.metrics"


def _has(records: list[logging.LogRecord], expected: str) -> bool:
    """Return True when at least one record's rendered message matches `expected`.

    Order-independent: parallel test runs interleave records from other loggers,
    so anchoring on `records[-1]` would be flaky. Logger-scoped capture (above)
    plus exact-message match is the stable contract.
    """
    return any(r.getMessage() == expected for r in records)


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
