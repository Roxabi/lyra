from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from .message import Message, Response
from .pool import Pool


class ChannelAdapter(Protocol):
    """Interface every channel adapter must implement.

    Security contract: adapters are responsible for verifying the identity
    of the sender (e.g. via platform token, signed webhook, or session)
    before constructing a Message. The hub trusts the user_id in the Message
    as authentic. Never set user_id from unverified inbound data.
    """

    async def send(self, original_msg: Message, response: Response) -> None: ...


class BindingKey(NamedTuple):
    """Routing key: (channel, user_id). Use user_id="*" for wildcard."""

    channel: str
    user_id: str


@dataclass(frozen=True)
class Binding:
    agent_name: str
    pool_id: str


class Hub:
    """Central hub: bounded async bus + adapter registry + bindings + pools.

    D3 scope: __init__, register_adapter, register_binding, resolve_binding.
    D4 scope: get_or_create_pool, run loop, dispatch_response.
    """

    BUS_SIZE = 100

    def __init__(self, bus_size: int = BUS_SIZE) -> None:
        self.bus: asyncio.Queue[Message] = asyncio.Queue(maxsize=bus_size)
        self.adapter_registry: dict[str, ChannelAdapter] = {}
        self.bindings: dict[BindingKey, Binding] = {}
        self.pools: dict[str, Pool] = {}
        # TODO(D4): get_or_create_pool() — use self.pools.setdefault(pool_id, Pool(...))
        # to avoid a TOCTOU race window; setdefault() is atomic in CPython.

    # ------------------------------------------------------------------
    # Adapter registry
    # ------------------------------------------------------------------

    def register_adapter(self, name: str, adapter: ChannelAdapter) -> None:
        """Register a channel adapter (e.g. "telegram", "discord").

        The adapter is responsible for authenticating inbound messages before
        placing them on the bus. See ChannelAdapter for the security contract.
        """
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

        Raises ValueError if pool_id is already assigned to a different user_id
        on the same channel — each pool must serve at most one user per channel.
        """
        # Check that pool_id is not already used by a different user on this channel
        for existing_key, existing_binding in self.bindings.items():
            if (
                existing_key.channel == channel
                and existing_key.user_id != user_id
                and existing_binding.pool_id == pool_id
            ):
                raise ValueError(
                    f"pool_id {pool_id!r} is already bound to user_id "
                    f"{existing_key.user_id!r} on channel {channel!r}. "
                    "Each pool must serve at most one user per channel."
                )
        self.bindings[BindingKey(channel, user_id)] = Binding(
            agent_name=agent_name,
            pool_id=pool_id,
        )

    def resolve_binding(self, msg: Message) -> Binding | None:
        """Return the binding for (channel, user_id), or None if not registered."""
        return self.bindings.get(BindingKey(msg.channel, msg.user_id))
