"""Forward-compatibility version check for NATS envelope payloads.

A receiver accepts any payload whose ``schema_version`` is less than or equal
to the receiver's own compiled-in ``SCHEMA_VERSION_*`` constant (forward-compat
rule).  Strictly-greater versions are dropped with an ERROR log line and an
optional in-process counter increment — instead of silently misinterpreting
unknown or removed fields.

Missing field: treated as version 1 (legacy backwards-compat).
Non-int / null / negative / zero: treated as malformed → drop.

Log rate limiting
-----------------
To avoid a log-flood DoS when a botched deploy produces thousands of mismatches
per second, drop logs are rate-limited per envelope name to one ERROR line every
``_LOG_INTERVAL_S`` seconds (default 60).  The counter still increments on every
drop — rate limiting only affects log volume, not telemetry.  The limiter is
module-level state intentionally (log volume is a process-wide concern, unlike
the counter which needs per-instance isolation for correctness).
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# Module-level rate-limit state: envelope_name → monotonic timestamp of last log.
# Shared across all NatsBus/NatsOutboundListener instances in the process; that
# is desirable for log volume (you want the WHOLE process rate-limited, not
# per-instance).  Tests should call ``_reset_log_state()`` between cases.
_last_log_ts: dict[str, float] = {}

# Minimum seconds between ERROR logs for the same envelope name.
_LOG_INTERVAL_S: float = 60.0


def check_schema_version(
    payload: dict,
    *,
    envelope_name: str,
    expected: int,
    subject: str | None = None,
    counter: dict[str, int] | None = None,
) -> bool:
    """Return True if payload is acceptable for this receiver; else drop + log + count.

    Rules
    -----
    - Missing field → treated as version 1 (legacy backwards compat).
    - Non-int value (string, null, float) → dropped.
    - Integer <= 0 → dropped (invalid range).
    - Integer > expected → dropped (forward-compat violation).
    - Integer in [1, expected] → accepted.

    Parameters
    ----------
    payload:
        The decoded JSON dict received from NATS.
    envelope_name:
        Human-readable name of the envelope type (e.g. ``"InboundMessage"``).
        Used as the counter key, rate-limit key, and in the log line.
    expected:
        The ``SCHEMA_VERSION_*`` constant this receiver was compiled against.
    subject:
        The NATS subject the message arrived on — included in the log line for
        easier triage.  ``None`` renders as ``"<unknown>"``.
    counter:
        Caller-owned mutable dict; incremented at ``counter[envelope_name]`` on
        every drop.  Pass ``None`` to skip counting.
    """
    raw = payload.get("schema_version", 1)

    # Non-int (including None, str, float) → drop.  ``bool`` is a subclass of
    # ``int`` in Python but we treat it as malformed since JSON ``true``/``false``
    # is never a valid version value.
    if not isinstance(raw, int) or isinstance(raw, bool):
        _drop(envelope_name, raw, expected, subject, counter)
        return False

    # Out-of-range integer → drop.
    if raw <= 0:
        _drop(envelope_name, raw, expected, subject, counter)
        return False

    # Forward-compat violation → drop.
    if raw > expected:
        _drop(envelope_name, raw, expected, subject, counter)
        return False

    # 1 <= raw <= expected → accept.
    return True


def _drop(
    envelope_name: str,
    raw_version: object,
    expected: int,
    subject: str | None,
    counter: dict[str, int] | None,
) -> None:
    """Record a dropped message: increment counter, emit rate-limited ERROR log."""
    # Counter increments unconditionally — rate limiting is log-only.
    if counter is not None:
        counter[envelope_name] = counter.get(envelope_name, 0) + 1

    # Rate-limited ERROR log: first drop per envelope fires immediately; repeats
    # within _LOG_INTERVAL_S are silent (but still counted).  After the interval
    # elapses, the next drop fires a fresh ERROR log.
    now = time.monotonic()
    last = _last_log_ts.get(envelope_name, 0.0)
    if now - last < _LOG_INTERVAL_S:
        return
    _last_log_ts[envelope_name] = now
    log.error(
        "NATS schema version mismatch — dropping message: envelope=%s "
        "payload_version=%r expected=%d subject=%s",
        envelope_name,
        raw_version,
        expected,
        subject or "<unknown>",
    )


def _reset_log_state() -> None:
    """Clear the module-level rate-limit state (test helper)."""
    _last_log_ts.clear()
