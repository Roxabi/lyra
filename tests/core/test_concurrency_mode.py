"""Unit tests for concurrency_mode_for_backend (#2130)."""

from __future__ import annotations

from factory.core.ports.active_jobs import concurrency_mode_for_backend


def test_omp_backends_are_steer() -> None:
    assert concurrency_mode_for_backend("omp-rpc") == "steer"
    assert concurrency_mode_for_backend("omp") == "steer"
    assert concurrency_mode_for_backend("OMP-RPC") == "steer"


def test_cli_and_unknown_are_queue() -> None:
    assert concurrency_mode_for_backend("claude-cli") == "queue"
    assert concurrency_mode_for_backend(None) == "queue"
    assert concurrency_mode_for_backend("") == "queue"
    assert concurrency_mode_for_backend("  ") == "queue"
