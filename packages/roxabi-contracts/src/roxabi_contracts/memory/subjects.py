"""Memory-domain NATS subject strings (roxabi-cortex satellite).

Cross-project peer namespace ``roxabi.memory.*`` (ADR-087) — not ``factory.*``.
"""

from dataclasses import dataclass
from typing import Literal

__all__ = ["SUBJECTS"]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace of memory-domain subject strings.

    Attribute access is pyright-checked: typos fail at type-check time.
    """

    # Consumers → cortex-memory (request-reply)
    query_assemble: Literal["roxabi.memory.query.assemble"] = (
        "roxabi.memory.query.assemble"
    )
    query_search: Literal["roxabi.memory.query.search"] = "roxabi.memory.query.search"
    capture: Literal["roxabi.memory.capture"] = "roxabi.memory.capture"
    # Satellite → registry
    heartbeat: Literal["roxabi.memory.heartbeat"] = "roxabi.memory.heartbeat"
    # Queue group for capture/search/assemble workers
    workers: Literal["memory-workers"] = "memory-workers"


SUBJECTS = _Subjects()
