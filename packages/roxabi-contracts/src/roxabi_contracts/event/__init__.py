"""Event/metric NATS contract surface.

Public API: SUBJECTS namespace + LyraEvent + LyraMetric envelope models.
The `fixtures` submodule is test-only — import explicitly as
``from roxabi_contracts.event.fixtures import ...``.
"""

from __future__ import annotations

from roxabi_contracts.event.models import LyraEvent, LyraMetric
from roxabi_contracts.event.subjects import (
    SUBJECTS,
    per_service_event,
    per_service_metric,
)

__all__ = [
    "LyraEvent",
    "LyraMetric",
    "SUBJECTS",
    "per_service_event",
    "per_service_metric",
]
