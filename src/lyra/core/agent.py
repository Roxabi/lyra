from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .message import Message, Response
from .pool import Pool


@dataclass(frozen=True)
class Agent:
    """Immutable configuration record for an agent."""

    name: str
    system_prompt: str
    memory_namespace: str
    permissions: tuple[str, ...] = field(default=())


class AgentBase(ABC):
    """Abstract base for concrete agent implementations.

    All mutable state lives in Pool.
    """

    def __init__(self, config: Agent) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    async def process(self, msg: Message, pool: Pool) -> Response: ...
