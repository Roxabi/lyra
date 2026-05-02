"""Structured telemetry helpers for the WorkerError envelope (spec C3).

All helpers emit via stdlib ``logging`` at INFO level using ``%s`` lazy
interpolation.  Format strings are pinned by spec C3 — do not alter wording,
spacing, or field order.
"""

import logging

import roxabi_contracts

log = logging.getLogger(__name__)


def emit_populated_total(domain: str) -> None:
    """Emit a METRIC line when a worker_error field is populated on an envelope."""
    log.info("METRIC worker_error_populated_total domain=%s count=1", domain)


def emit_received_total(code: str, domain: str) -> None:
    """Emit a METRIC line when a worker_error is extracted at the hub boundary."""
    log.info(
        "METRIC worker_error_received_total code=%s domain=%s count=1", code, domain
    )


def log_contracts_version() -> None:
    """Log the installed roxabi_contracts version at process startup."""
    log.info("roxabi_contracts_version=%s", roxabi_contracts.__version__)
