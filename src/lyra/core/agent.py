from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .message import Message
from .pool import Pool


@dataclass
class Response:
    content: str
    metadata: dict = field(default_factory=dict)


class ChannelAdapter(Protocol):
    """Interface every channel adapter must implement."""

    async def send(self, original_msg: Message, response: Response) -> None: ...


@dataclass(frozen=True)
class Agent:
    """Stateless singleton. All mutable state lives in Pool — no race conditions."""

    name: str
    system_prompt: str
    memory_namespace: str
    permissions: tuple[str, ...] = field(default_factory=tuple)

    async def process(self, msg: Message, pool: Pool) -> Response:
        raise NotImplementedError
