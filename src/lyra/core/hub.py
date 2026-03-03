from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from .message import Message, Platform, Response
from .pool import Pool

log = logging.getLogger(__name__)


class ChannelAdapter(Protocol):
    """Interface every channel adapter must implement.

    Security contract: adapters are responsible for verifying the identity
    of the sender (e.g. via platform token, signed webhook, or session)
    before constructing a Message. The hub trusts the user_id in the Message
    as authentic. Never set user_id from unverified inbound data.
    """

    async def send(self, original_msg: Message, response: Response) -> None: ...


class RoutingKey(NamedTuple):
    """Routing key: (platform, bot_id, user_id). Use user_id='*' for wildcard."""

    platform: Platform
    bot_id: str
    user_id: str


@dataclass(frozen=True)
class Binding:
    agent_name: str
    pool_id: str


class Hub:
    """Central hub: bounded async bus + adapter registry + bindings + pools."""

    BUS_SIZE = 100

    def __init__(self, bus_size: int = BUS_SIZE) -> None:
        self.bus: asyncio.Queue[Message] = asyncio.Queue(maxsize=bus_size)
        self.adapter_registry: dict[tuple[Platform, str], ChannelAdapter] = {}
        self.bindings: dict[RoutingKey, Binding] = {}
        self.pools: dict[str, Pool] = {}

    # ------------------------------------------------------------------
    # Adapter registry
    # ------------------------------------------------------------------

    def register_adapter(
        self, platform: Platform, bot_id: str, adapter: ChannelAdapter
    ) -> None:
        """Register a channel adapter keyed by (platform, bot_id).

        The adapter is responsible for authenticating inbound messages before
        placing them on the bus. See ChannelAdapter for the security contract.
        """
        self.adapter_registry[(platform, bot_id)] = adapter

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def register_binding(
        self,
        platform: Platform,
        bot_id: str,
        user_id: str,
        agent_name: str,
        pool_id: str,
    ) -> None:
        """Map (platform, bot_id, user_id) -> (agent_name, pool_id).

        Raises ValueError if pool_id is already assigned to a different user_id
        on the same (platform, bot_id) — each pool must serve at most one user
        per (platform, bot_id) pair.
        """
        for existing_key, existing_binding in self.bindings.items():
            if (
                existing_key.platform == platform
                and existing_key.bot_id == bot_id
                and existing_key.user_id != user_id
                and existing_binding.pool_id == pool_id
            ):
                raise ValueError(
                    f"pool_id {pool_id!r} is already bound to user_id "
                    f"{existing_key.user_id!r} on {platform}:{bot_id}. "
                    "Each pool must serve at most one user per (platform, bot_id)."
                )
        self.bindings[RoutingKey(platform, bot_id, user_id)] = Binding(
            agent_name=agent_name,
            pool_id=pool_id,
        )

    def resolve_binding(self, msg: Message) -> Binding | None:
        """Resolve binding: exact key, then wildcard fallback, else None."""
        key = RoutingKey(msg.platform, msg.bot_id, msg.user_id)
        exact = self.bindings.get(key)
        if exact is not None:
            return exact
        wildcard = RoutingKey(msg.platform, msg.bot_id, "*")
        return self.bindings.get(wildcard)

    # ------------------------------------------------------------------
    # Pools
    # ------------------------------------------------------------------

    def get_or_create_pool(self, pool_id: str, agent_name: str) -> Pool:
        """Return existing pool or create a new one. Atomic via CPython dict."""
        if pool_id not in self.pools:
            self.pools[pool_id] = Pool(pool_id=pool_id, agent_name=agent_name)
        return self.pools[pool_id]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch_response(self, msg: Message, response: Response) -> None:
        """Send response back via the originating adapter."""
        adapter = self.adapter_registry[(msg.platform, msg.bot_id)]
        await adapter.send(msg, response)

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Hub bus consumer loop. Runs until cancelled."""
        while True:
            msg = await self.bus.get()
            try:
                binding = self.resolve_binding(msg)
                if binding is None:
                    log.warning(
                        "unmatched routing key %s — message dropped",
                        RoutingKey(msg.platform, msg.bot_id, msg.user_id),
                    )
                    continue
                pool = self.get_or_create_pool(binding.pool_id, binding.agent_name)
                async with pool.lock:
                    # agent.process() wired in Slice 2 (Telegram adapter)
                    pass
            finally:
                self.bus.task_done()
