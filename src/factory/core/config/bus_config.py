"""BusConfig — frozen dataclass holding LocalBus sizing defaults.

Centralises the three magic integers that govern LocalBus capacity so
they can be referenced by name instead of scattered bare literals.
No behavioural change — default values remain identical to the previous
inline defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BusConfig:
    """Sizing configuration for LocalBus.

    Attributes:
        DEFAULT_MAXSIZE: Per-platform queue maxsize (items).
        DEFAULT_QUEUE_DEPTH: Staging queue depth above which a warning
            is emitted (queue_depth_threshold).
        DEFAULT_STAGING_MAXSIZE: Staging queue maxsize (items).
    """

    DEFAULT_MAXSIZE: int = 100  # const-ok: named config default
    DEFAULT_QUEUE_DEPTH: int = 100  # const-ok: named config default
    DEFAULT_STAGING_MAXSIZE: int = 500  # const-ok: named config default
