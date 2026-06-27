"""Worker heartbeat freshness tracker for dashboard BFF."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

HB_TTL = 30.0  # const-ok: dashboard harness freshness window


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