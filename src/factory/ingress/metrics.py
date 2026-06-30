"""Ingress operational counters (in-process; LogQL for alerting)."""

from __future__ import annotations

from collections import Counter
from threading import Lock

_lock = Lock()
_unknown_installation: Counter[str] = Counter()


def record_unknown_installation(connector: str) -> None:
    with _lock:
        _unknown_installation[connector] += 1


def unknown_installation_total(connector: str) -> int:
    with _lock:
        return int(_unknown_installation[connector])


def reset_metrics() -> None:
    """Test helper."""
    with _lock:
        _unknown_installation.clear()