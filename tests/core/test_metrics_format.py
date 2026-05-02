"""Unit tests — verify spec C3 log format strings for WorkerError metrics helpers."""

import roxabi_contracts
from lyra.core.messaging.metrics import (
    emit_populated_total,
    emit_received_total,
    log_contracts_version,
)


def test_emit_populated_total_format(caplog):
    with caplog.at_level("INFO"):
        emit_populated_total("cli")
    expected = "METRIC worker_error_populated_total domain=cli count=1"
    assert caplog.records[-1].getMessage() == expected


def test_emit_received_total_format(caplog):
    with caplog.at_level("INFO"):
        emit_received_total("transport.timeout", "llm")
    expected = (
        "METRIC worker_error_received_total code=transport.timeout domain=llm count=1"
    )
    assert caplog.records[-1].getMessage() == expected


def test_log_contracts_version_format(caplog):
    with caplog.at_level("INFO"):
        log_contracts_version()
    expected = f"roxabi_contracts_version={roxabi_contracts.__version__}"
    assert caplog.records[-1].getMessage() == expected
