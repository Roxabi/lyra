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
    history: list[Message] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock
