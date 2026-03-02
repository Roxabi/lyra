from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .message import Message


@dataclass
class Pool:
    """One pool per (channel, user). Holds conversation history and a per-user lock.

    The lock enforces sequential processing per user while allowing full
    parallelism across different users.
    """

    pool_id: str
    agent_name: str
    # TODO: decide eviction strategy before adding memory layer:
    #   option A: deque(maxlen=N) for sliding-window compaction
    #   option B: Pool.append(msg) mutator with compaction callback (Level 0→3 cascade)
    history: list[Message] = field(default_factory=list)
    _lock: asyncio.Lock = field(
        init=False, repr=False, compare=False, default_factory=asyncio.Lock
    )

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock
