"""Event/metric NATS subject strings and per-service helpers.

Canonical values — literal strings (no f-strings, no derivation) so grep
can locate every reference across the monorepo.

Subject hierarchy:
  lyra.event.<service>.<kind>   — operational events
  lyra.metric.<service>.<name>  — typed metrics
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from roxabi_contracts._nats_utils import _SAFE_SUBJECT_SEGMENT_RE

__all__ = ["SUBJECTS", "per_service_event", "per_service_metric"]

# Namespaced tokens (e.g. "lifecycle.swap", "request.count") are valid
# multi-segment NATS subject suffixes. Dots are allowed for namespacing,
# but leading/trailing/consecutive dots and wildcards are rejected.
_SAFE_NAMESPACED_RE = re.compile(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*")


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace of event/metric subject strings.

    Attribute access is pyright-checked: typos fail at type-check time
    rather than silently returning None.
    """

    event_all: Literal["lyra.event.>"] = "lyra.event.>"
    metric_all: Literal["lyra.metric.>"] = "lyra.metric.>"


SUBJECTS = _Subjects()


def _validate_segment(segment: str) -> None:
    if not _SAFE_SUBJECT_SEGMENT_RE.fullmatch(segment):
        raise ValueError(
            f"NATS subject segment must match [A-Za-z0-9_-]+ (got {segment!r}); "
            "dots, wildcards (* >) and empty segments are rejected"
        )


def _validate_namespaced(token: str) -> None:
    if not _SAFE_NAMESPACED_RE.fullmatch(token):
        raise ValueError(
            f"NATS namespaced token must match [A-Za-z0-9_-]+(\\.[A-Za-z0-9_-]+)* "
            f"(got {token!r}); wildcards (* >) and dot-boundary violations are rejected"
        )


def per_service_event(service: str, kind: str) -> str:
    """Per-service event subject: ``lyra.event.{service}.{kind}``.

    Raises ``ValueError`` if ``service`` contains characters outside
    ``[A-Za-z0-9_-]`` or if ``kind`` is not a valid namespaced token.
    """
    _validate_segment(service)
    _validate_namespaced(kind)
    return f"lyra.event.{service}.{kind}"


def per_service_metric(service: str, name: str) -> str:
    """Per-service metric subject: ``lyra.metric.{service}.{name}``.

    Raises ``ValueError`` if ``service`` contains characters outside
    ``[A-Za-z0-9_-]`` or if ``name`` is not a valid namespaced token.
    """
    _validate_segment(service)
    _validate_namespaced(name)
    return f"lyra.metric.{service}.{name}"
