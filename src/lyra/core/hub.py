from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .message import Message
from .pool import Pool

if TYPE_CHECKING:
    from .agent import ChannelAdapter

# (channel, user_id) — use "*" as user_id wildcard for an entire channel
BindingKey = tuple[str, str]


@dataclass
class Binding:
    agent_name: str
    pool_id: str


class Hub:
    """Central hub: bounded async bus + adapter registry + bindings + pools.

    D3 scope: __init__, register_adapter, register_binding, resolve_binding.
    D4 scope: get_or_create_pool, run loop, dispatch_response.
    """

    BUS_SIZE = 100

    def __init__(self) -> None:
        self.bus: asyncio.Queue[Message] = asyncio.Queue(maxsize=self.BUS_SIZE)
        self.adapter_registry: dict[str, ChannelAdapter] = {}
        self.bindings: dict[BindingKey, Binding] = {}
        self.pools: dict[str, Pool] = {}

    # ------------------------------------------------------------------
    # Adapter registry
    # ------------------------------------------------------------------

    def register_adapter(self, name: str, adapter: ChannelAdapter) -> None:
        """Register a channel adapter (e.g. "telegram", "discord")."""
        self.adapter_registry[name] = adapter

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def register_binding(
        self,
        channel: str,
        user_id: str,
        agent_name: str,
        pool_id: str,
    ) -> None:
        """Map (channel, user_id) → (agent_name, pool_id).

        Use user_id="*" for a wildcard that matches any user on that channel.
        """
        self.bindings[(channel, user_id)] = Binding(
            agent_name=agent_name,
            pool_id=pool_id,
        )

    def resolve_binding(self, msg: Message) -> Binding | None:
        """Return the binding for msg, falling back to the channel wildcard."""
        return self.bindings.get((msg.channel, msg.user_id)) or self.bindings.get(
            (msg.channel, "*")
        )
