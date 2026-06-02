from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LifecycleConfig:
    """Immutable lifecycle defaults for debouncer and circuit breaker.

    All values are class attributes (not instance fields) so they can be
    referenced without instantiation: ``LifecycleConfig.DEFAULT_DEBOUNCE_MS``.
    """

    DEFAULT_DEBOUNCE_MS: int = 300  # const-ok: named config default
    CIRCUIT_FAILURE_THRESHOLD: int = 5
    CIRCUIT_RECOVERY_TIMEOUT: int = 60  # const-ok: named config default
    MAX_MERGED_CHARS: int = 4096  # const-ok: named config default
