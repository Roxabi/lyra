"""Voice worker registry + load-aware scoring.

Consumes heartbeat payloads from STT/TTS adapters, maintains a live-worker
registry scored by ``(active_requests, vram_used_pct)``, and exposes selection
helpers used by the hub-side clients (``NatsSttClient`` / ``NatsTtsClient``) to
pick the least-loaded worker before routing a request.

Scoring: ``score = active_requests * active_weight + vram_used_pct * vram_weight``.
Lower is better. Workers without VRAM data (``vram_total_mb=0``) contribute only
the ``active_requests`` term.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

DEFAULT_HB_TTL = 15.0
DEFAULT_ACTIVE_WEIGHT = 100.0
DEFAULT_VRAM_WEIGHT = 50.0


@dataclass
class WorkerStats:
    worker_id: str
    last_heartbeat: float  # monotonic seconds
    vram_used_mb: int = 0
    vram_total_mb: int = 0
    active_requests: int = 0


class VoiceWorkerRegistry:
    def __init__(
        self,
        *,
        hb_ttl: float = DEFAULT_HB_TTL,
        active_weight: float = DEFAULT_ACTIVE_WEIGHT,
        vram_weight: float = DEFAULT_VRAM_WEIGHT,
    ) -> None:
        self._workers: dict[str, WorkerStats] = {}
        self._hb_ttl = hb_ttl
        self._active_weight = active_weight
        self._vram_weight = vram_weight

    @staticmethod
    def _coerce_int(value: object) -> int:
        if value is None:
            return 0
        try:
            return int(value)  # type: ignore[arg-type]  # best-effort JSON coercion
        except (TypeError, ValueError):
            return 0

    def record_heartbeat(self, payload: dict) -> None:
        """Upsert a worker entry from a heartbeat payload.

        Silently ignores payloads without a ``worker_id`` — the caller logs.
        """
        worker_id = payload.get("worker_id")
        if not isinstance(worker_id, str) or not worker_id:
            return
        self._workers[worker_id] = WorkerStats(
            worker_id=worker_id,
            last_heartbeat=time.monotonic(),
            vram_used_mb=self._coerce_int(payload.get("vram_used_mb")),
            vram_total_mb=self._coerce_int(payload.get("vram_total_mb")),
            active_requests=self._coerce_int(payload.get("active_requests")),
        )

    def _prune(self) -> None:
        now = time.monotonic()
        horizon = self._hb_ttl * 2
        self._workers = {
            k: v
            for k, v in self._workers.items()
            if now - v.last_heartbeat <= horizon
        }

    def alive_workers(self) -> list[WorkerStats]:
        self._prune()
        now = time.monotonic()
        return [
            w for w in self._workers.values() if now - w.last_heartbeat <= self._hb_ttl
        ]

    def any_alive(self) -> bool:
        return bool(self.alive_workers())

    def score(self, w: WorkerStats) -> float:
        vram_pct = (
            (w.vram_used_mb / w.vram_total_mb) if w.vram_total_mb > 0 else 0.0
        )
        return w.active_requests * self._active_weight + vram_pct * self._vram_weight

    def pick_least_loaded(self) -> WorkerStats | None:
        alive = self.alive_workers()
        if not alive:
            return None
        return min(alive, key=lambda w: (self.score(w), w.worker_id))
