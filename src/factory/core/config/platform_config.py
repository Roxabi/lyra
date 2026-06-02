from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformConfig:
    """Immutable platform-level defaults for context and compaction thresholds.

    All values are class attributes (not instance fields) so they can be
    referenced without instantiation: ``PlatformConfig.DEFAULT_CONTEXT_TOKENS``.
    """

    DEFAULT_CONTEXT_TOKENS: int = 200_000  # const-ok: named config default
    COMPACT_THRESHOLD: int = int(0.8 * 200_000)  # 160_000
    COMPACT_TAIL: int = 10  # const-ok: named config default
