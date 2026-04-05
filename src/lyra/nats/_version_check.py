"""Forward-compatibility version check for NATS envelope payloads.

A receiver accepts any payload whose ``schema_version`` is less than or equal
to the receiver's own compiled-in ``SCHEMA_VERSION_*`` constant (forward-compat
rule).  Strictly-greater versions are dropped with a single ERROR log line and
an optional in-process counter increment — instead of silently misinterpreting
unknown or removed fields.

Missing field: treated as version 1 (legacy backwards-compat).
Non-int / null / negative / zero: treated as malformed → drop.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


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
        Used as the counter key and in the log line.
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

    # Non-int (including None, str, float) → drop.
    if not isinstance(raw, int):
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
    log.error(
        "NATS schema version mismatch — dropping message: envelope=%s "
        "payload_version=%r expected=%d subject=%s",
        envelope_name,
        raw_version,
        expected,
        subject or "<unknown>",
    )
    if counter is not None:
        counter[envelope_name] = counter.get(envelope_name, 0) + 1
