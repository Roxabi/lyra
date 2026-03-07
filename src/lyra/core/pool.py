from __future__ import annotations

import asyncio
from collections import deque
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
    sdk_history: deque[dict] = field(default_factory=deque)
    max_sdk_history: int = 50
    _lock: asyncio.Lock = field(
        init=False, repr=False, compare=False, default_factory=asyncio.Lock
    )

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def extend_sdk_history(self, new_messages: list[dict]) -> None:
        """Append messages from an exchange and trim to cap."""
        self.sdk_history.extend(new_messages)
        while len(self.sdk_history) > self.max_sdk_history:
            self.sdk_history.popleft()
