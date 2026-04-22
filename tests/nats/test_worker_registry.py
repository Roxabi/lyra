"""Tests for WorkerRegistry — scoring, selection, freshness pruning."""

from __future__ import annotations

import time

import pytest

from lyra.nats.worker_registry import (
    DEFAULT_ACTIVE_WEIGHT,
    DEFAULT_VRAM_WEIGHT,
    MAX_WORKERS,
    WorkerRegistry,
    WorkerStats,
)


def _hb(worker_id: str, **kwargs: int) -> dict:
    base: dict = {"worker_id": worker_id}
    base.update(kwargs)
    return base


class TestRecordHeartbeat:
    def test_upserts_worker(self) -> None:
        reg = WorkerRegistry()
        reg.record_heartbeat(_hb("w1", vram_used_mb=1000, vram_total_mb=16000))
        alive = reg.alive_workers()
        assert len(alive) == 1
        assert alive[0].worker_id == "w1"
        assert alive[0].vram_used_mb == 1000
        assert alive[0].vram_total_mb == 16000
        assert alive[0].active_requests == 0

    def test_missing_worker_id_is_ignored(self) -> None:
        reg = WorkerRegistry()
        reg.record_heartbeat({"vram_used_mb": 1000})
        reg.record_heartbeat({"worker_id": ""})
        reg.record_heartbeat({"worker_id": None})  # type: ignore[dict-item]
        assert reg.alive_workers() == []

    def test_invalid_worker_id_rejected(self) -> None:
        """Subject-injection guard: wildcard / space chars rejected at ingress."""
        reg = WorkerRegistry()
        for bad_id in ("*.evil", "foo.>", "has space", "pipe|bad", ">"):
            reg.record_heartbeat({"worker_id": bad_id})
        assert reg.alive_workers() == []

    def test_cap_drops_new_ids_at_max(self) -> None:
        """Hard cap drops new worker ids past ``MAX_WORKERS``; existing ones update."""
        reg = WorkerRegistry()
        for i in range(MAX_WORKERS):
            reg.record_heartbeat({"worker_id": f"w-{i}"})
        assert len(reg.alive_workers()) == MAX_WORKERS
        # New id past cap is dropped.
        reg.record_heartbeat({"worker_id": "overflow"})
        assert "overflow" not in {w.worker_id for w in reg.alive_workers()}
        # Existing id still updates.
        reg.record_heartbeat({"worker_id": "w-0", "active_requests": 42})
        w0 = next(w for w in reg.alive_workers() if w.worker_id == "w-0")
        assert w0.active_requests == 42

    def test_coerces_numeric_fields(self) -> None:
        reg = WorkerRegistry()
        reg.record_heartbeat(
            {
                "worker_id": "w1",
                "vram_used_mb": "2400",  # JSON sometimes delivers strings
                "vram_total_mb": "16384",
                "active_requests": None,
            }
        )
        alive = reg.alive_workers()
        assert alive[0].vram_used_mb == 2400
        assert alive[0].vram_total_mb == 16384
        assert alive[0].active_requests == 0

    def test_updates_existing_worker(self) -> None:
        reg = WorkerRegistry()
        reg.record_heartbeat(_hb("w1", active_requests=0))
        reg.record_heartbeat(_hb("w1", active_requests=3))
        alive = reg.alive_workers()
        assert len(alive) == 1
        assert alive[0].active_requests == 3


class TestPruning:
    def test_evicts_entries_older_than_ttl_times_two(self) -> None:
        reg = WorkerRegistry(hb_ttl=15.0)
        reg._workers["ancient"] = WorkerStats(
            worker_id="ancient",
            last_heartbeat=time.monotonic() - 35.0,
        )
        reg._workers["stale-but-kept"] = WorkerStats(
            worker_id="stale-but-kept",
            last_heartbeat=time.monotonic() - 20.0,
        )
        reg._workers["fresh"] = WorkerStats(
            worker_id="fresh",
            last_heartbeat=time.monotonic(),
        )
        # alive_workers() prunes as a side-effect before filtering.
        _ = reg.alive_workers()
        assert "ancient" not in reg._workers
        # stale-but-kept is past TTL but within TTL*2 — retained for potential
        # re-arrival of heartbeat.
        assert "stale-but-kept" in reg._workers
        assert "fresh" in reg._workers

    def test_alive_only_returns_within_ttl(self) -> None:
        reg = WorkerRegistry(hb_ttl=15.0)
        reg._workers["fresh"] = WorkerStats(
            worker_id="fresh",
            last_heartbeat=time.monotonic() - 5.0,
        )
        reg._workers["stale"] = WorkerStats(
            worker_id="stale",
            last_heartbeat=time.monotonic() - 20.0,
        )
        alive_ids = {w.worker_id for w in reg.alive_workers()}
        assert alive_ids == {"fresh"}


class TestScoring:
    def test_score_formula_active_plus_vram_pct(self) -> None:
        reg = WorkerRegistry(
            active_weight=DEFAULT_ACTIVE_WEIGHT, vram_weight=DEFAULT_VRAM_WEIGHT
        )
        w = WorkerStats(
            worker_id="w",
            last_heartbeat=time.monotonic(),
            vram_used_mb=8000,
            vram_total_mb=16000,  # 0.5
            active_requests=2,
        )
        # 2 * 100 + 0.5 * 50 = 225
        assert reg.score(w) == pytest.approx(225.0)

    def test_score_when_vram_total_zero(self) -> None:
        reg = WorkerRegistry()
        w = WorkerStats(
            worker_id="w",
            last_heartbeat=time.monotonic(),
            vram_used_mb=1000,
            vram_total_mb=0,
            active_requests=1,
        )
        # Only active-requests contributes.
        assert reg.score(w) == pytest.approx(DEFAULT_ACTIVE_WEIGHT)


class TestSelection:
    def test_pick_none_when_no_workers(self) -> None:
        reg = WorkerRegistry()
        assert reg.pick_least_loaded() is None

    def test_pick_only_fresh_worker(self) -> None:
        reg = WorkerRegistry()
        reg._workers["stale"] = WorkerStats(
            worker_id="stale",
            last_heartbeat=time.monotonic() - 20.0,
        )
        reg.record_heartbeat(_hb("fresh"))
        pick = reg.pick_least_loaded()
        assert pick is not None and pick.worker_id == "fresh"

    def test_pick_lowest_vram_pct_when_tied_active(self) -> None:
        reg = WorkerRegistry()
        reg.record_heartbeat(_hb("heavy", vram_used_mb=12000, vram_total_mb=16000))
        reg.record_heartbeat(_hb("light", vram_used_mb=2000, vram_total_mb=16000))
        pick = reg.pick_least_loaded()
        assert pick is not None and pick.worker_id == "light"

    def test_active_requests_dominate_vram(self) -> None:
        """A fuller-VRAM but idle worker beats a lighter-VRAM busy worker."""
        reg = WorkerRegistry()
        reg.record_heartbeat(
            _hb("busy-light", vram_used_mb=2000, vram_total_mb=16000, active_requests=1)
        )
        reg.record_heartbeat(
            _hb(
                "idle-full",
                vram_used_mb=14000,
                vram_total_mb=16000,
                active_requests=0,
            )
        )
        # busy-light: 100 + 6.25 = 106.25
        # idle-full: 0   + 43.75 = 43.75
        pick = reg.pick_least_loaded()
        assert pick is not None and pick.worker_id == "idle-full"

    def test_deterministic_tiebreak_by_worker_id(self) -> None:
        """When scores tie, the lexically smallest worker_id wins."""
        reg = WorkerRegistry()
        reg.record_heartbeat(_hb("worker-b"))
        reg.record_heartbeat(_hb("worker-a"))
        pick = reg.pick_least_loaded()
        assert pick is not None and pick.worker_id == "worker-a"

    def test_any_alive_true_when_one_fresh(self) -> None:
        reg = WorkerRegistry()
        reg.record_heartbeat(_hb("w"))
        assert reg.any_alive() is True

    def test_any_alive_false_when_empty(self) -> None:
        reg = WorkerRegistry()
        assert reg.any_alive() is False


class TestOrderedByScore:
    """Tests for ordered_by_score() — returns workers sorted by score ascending."""

    def test_returns_sorted_by_score(self) -> None:
        """Workers returned in ascending score order."""
        reg = WorkerRegistry()
        reg.record_heartbeat(
            _hb("w-heavy", vram_used_mb=12000, vram_total_mb=16000, active_requests=2)
        )
        reg.record_heartbeat(
            _hb("w-light", vram_used_mb=2000, vram_total_mb=16000, active_requests=0)
        )
        reg.record_heartbeat(
            _hb("w-mid", vram_used_mb=8000, vram_total_mb=16000, active_requests=1)
        )
        ordered = reg.ordered_by_score()
        assert len(ordered) == 3
        # w-light: 0*100 + 0.125*50 = 6.25
        # w-mid:   1*100 + 0.5*50   = 125.0
        # w-heavy: 2*100 + 0.75*50  = 237.5
        assert ordered[0].worker_id == "w-light"
        assert ordered[1].worker_id == "w-mid"
        assert ordered[2].worker_id == "w-heavy"

    def test_returns_empty_when_no_alive(self) -> None:
        """Returns [] when no alive workers."""
        reg = WorkerRegistry(hb_ttl=15.0)
        reg._workers["stale"] = WorkerStats(
            worker_id="stale",
            last_heartbeat=time.monotonic() - 20.0,
        )
        assert reg.ordered_by_score() == []

    def test_tiebreaker_by_worker_id(self) -> None:
        """Tied scores sorted by worker_id alphabetically."""
        reg = WorkerRegistry()
        # All have same active_requests=0, vram_total_mb=0 → score=0
        reg.record_heartbeat(_hb("worker-c"))
        reg.record_heartbeat(_hb("worker-a"))
        reg.record_heartbeat(_hb("worker-b"))
        ordered = reg.ordered_by_score()
        assert len(ordered) == 3
        assert ordered[0].worker_id == "worker-a"
        assert ordered[1].worker_id == "worker-b"
        assert ordered[2].worker_id == "worker-c"


class TestMarkStale:
    """Tests for mark_stale() — manually exclude a worker from alive set."""

    def test_excludes_from_alive_workers(self) -> None:
        """mark_stale(w) excludes w from alive_workers()."""
        reg = WorkerRegistry()
        reg.record_heartbeat(_hb("w1"))
        reg.record_heartbeat(_hb("w2"))
        reg.mark_stale("w1")
        alive = reg.alive_workers()
        assert len(alive) == 1
        assert alive[0].worker_id == "w2"

    def test_idempotent(self) -> None:
        """Calling mark_stale twice on same worker is safe."""
        reg = WorkerRegistry()
        reg.record_heartbeat(_hb("w1"))
        reg.mark_stale("w1")
        reg.mark_stale("w1")  # Should not raise
        assert reg.alive_workers() == []

    def test_readmit_on_heartbeat(self) -> None:
        """mark_stale'd worker re-admitted when heartbeat arrives."""
        reg = WorkerRegistry()
        reg.record_heartbeat(_hb("w1"))
        reg.mark_stale("w1")
        assert "w1" not in {w.worker_id for w in reg.alive_workers()}
        # New heartbeat should readmit
        reg.record_heartbeat(_hb("w1", active_requests=5))
        alive = reg.alive_workers()
        assert len(alive) == 1
        assert alive[0].worker_id == "w1"
        assert alive[0].active_requests == 5

    def test_does_not_delete_entry(self) -> None:
        """mark_stale mutates last_heartbeat, does NOT delete from _workers."""
        reg = WorkerRegistry()
        reg.record_heartbeat(_hb("w1"))
        assert "w1" in reg._workers
        reg.mark_stale("w1")
        # Entry should still exist (not deleted)
        assert "w1" in reg._workers
        # But should not appear in alive_workers()
        assert "w1" not in {w.worker_id for w in reg.alive_workers()}

    def test_unknown_worker_id_is_safe(self) -> None:
        """Calling mark_stale on unknown worker_id is safe (no-op)."""
        reg = WorkerRegistry()
        reg.mark_stale("nonexistent")  # Should not raise
        assert reg.alive_workers() == []
