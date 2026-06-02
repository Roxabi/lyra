"""Memory types — shared data structures for the memory layer.

Provides:
- SessionSnapshot: frozen dataclass capturing pool state at flush time
- FRESHNESS_TTL_DAYS: per-type staleness thresholds
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    user_id: str
    medium: str
    agent_namespace: str
    session_start: datetime
    session_end: datetime
    message_count: int
    source_turns: int


FRESHNESS_TTL_DAYS: dict[str, int | None] = {
    "concept:technology": 180,
    "concept:project": 90,
    "concept:decision": None,
    "concept:fact": 60,
    "concept:entity": 180,
    "preference": 30,
}
