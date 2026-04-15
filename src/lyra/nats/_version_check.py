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


def check_contract_version(
    payload: dict,
    *,
    envelope_name: str,
    expected: str,
    subject: str | None = None,
    counter: dict[str, int] | None = None,
) -> bool:
    """Return True if payload's ``contract_version`` is acceptable for this receiver.

    Mirrors :func:`check_schema_version` but validates the ADR-044 wire-format
    ``contract_version`` field (a numeric string such as ``"1"``).  Rejects
    envelopes whose contract_version (parsed as int) is strictly greater than
    the receiver's compiled-in ``CONTRACT_VERSION`` — an outdated hub must not
    silently process payloads written against a newer contract.

    Rules
    -----
    - Missing field → treated as ``"1"`` (legacy backwards compat).
    - Non-str value (int, None, float, bool, list, ...) → dropped.
    - String that doesn't parse as an int → dropped.
    - Parsed int <= 0 → dropped.
    - Parsed int > parsed expected → dropped (forward-compat violation).
    - Parsed int in [1, expected] → accepted.

    Parameters
    ----------
    expected:
        The hub's ``CONTRACT_VERSION`` constant (a numeric string).  Must parse
        as a positive int — caller is responsible (see the module-level assert
        in ``adapter_base.py``).
    """
    raw = payload.get("contract_version", "1")

    # Wire format (ADR-044) stamps contract_version as a numeric string. Reject
    # anything else — including bare int — to keep the validator symmetric with
    # producer behavior and prevent silent lenience from masking wire drift.
    if not isinstance(raw, str):
        _drop(envelope_name, raw, expected, subject, counter, kind="contract")
        return False

    try:
        payload_v = int(raw)
    except ValueError:
        _drop(envelope_name, raw, expected, subject, counter, kind="contract")
        return False

    expected_v = int(expected)  # asserted parseable at module load in adapter_base

    if payload_v <= 0:
        _drop(envelope_name, raw, expected, subject, counter, kind="contract")
        return False

    if payload_v > expected_v:
        _drop(envelope_name, raw, expected, subject, counter, kind="contract")
        return False

    return True


def _drop(  # noqa: PLR0913
    envelope_name: str,
    raw_version: object,
    expected: int | str,
    subject: str | None,
    counter: dict[str, int] | None,
    *,
    kind: str = "schema",
) -> None:
    """Record a dropped message: increment counter, emit rate-limited ERROR log.

    Both counter and rate-limit state are keyed on ``f"{envelope_name}:{kind}"``
    so schema and contract drops stay independent — a flood of one kind does
    not silence logs or skew telemetry for the other.
    """
    key = f"{envelope_name}:{kind}"

    # Counter increments unconditionally — rate limiting is log-only.
    if counter is not None:
        counter[key] = counter.get(key, 0) + 1

    # Rate-limited ERROR log: first drop per (envelope, kind) fires immediately;
    # repeats within _LOG_INTERVAL_S are silent (but still counted).  After the
    # interval elapses, the next drop fires a fresh ERROR log.
    now = time.monotonic()
    last = _last_log_ts.get(key, 0.0)
    if now - last < _LOG_INTERVAL_S:
        return
    _last_log_ts[key] = now
    log.error(
        "NATS %s version mismatch — dropping message: envelope=%s "
        "payload_version=%r expected=%r subject=%s",
        kind,
        envelope_name,
        raw_version,
        expected,
        subject or "<unknown>",
    )


def _reset_log_state() -> None:
    """Clear the module-level rate-limit state (test helper)."""
    _last_log_ts.clear()
