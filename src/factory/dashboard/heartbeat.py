"""Worker heartbeat freshness tracker for dashboard BFF."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

HB_TTL = 30.0  # const-ok: dashboard harness freshness window

CLIPOOL_QUEUE = "clipool-workers"
OMP_QUEUE = "omp-workers"


@dataclass
class HeartbeatTracker:
    """Tracks last-seen heartbeat monotonic timestamps per worker role."""

    _freshness: dict[str, float] = field(default_factory=dict)

    def mark(self, worker_id: str) -> None:
        self._freshness[worker_id] = time.monotonic()

    def is_alive(self, worker_id: str) -> bool:
        ts = self._freshness.get(worker_id)
        if ts is None:
            return False
        return (time.monotonic() - ts) <= HB_TTL


def queue_group_alive(freshness: Any, queue_group: str) -> bool:
    """True when any heartbeat for *queue_group* arrived within :data:`HB_TTL`.

    NatsAdapterBase publishes ``worker_id = f"{queue_group}-{host}-{pid}"``.
    """
    if not isinstance(freshness, dict):
        return False
    now = time.monotonic()
    prefix = f"{queue_group}-"
    for worker_id, ts in freshness.items():
        if not isinstance(worker_id, str):
            continue
        if worker_id != queue_group and not worker_id.startswith(prefix):
            continue
        try:
            seen_at = float(ts)
        except (TypeError, ValueError):
            continue
        if (now - seen_at) <= HB_TTL:
            return True
    return False