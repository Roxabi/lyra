"""Registration mixin for Hub — split from hub.py (#760).

Provides adapter / dispatcher / authenticator / agent / binding / store
registration methods. Pure delegation to instance attributes owned by Hub.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..messaging.message import InboundMessage, Platform
from .hub_protocol import Binding, ChannelAdapter, RoutingKey

if TYPE_CHECKING:
    from lyra.infrastructure.stores.identity_alias_store import IdentityAliasStore
    from lyra.infrastructure.stores.turn_store import TurnStore

    from ..agent import AgentBase
    from ..auth.authenticator import Authenticator
    from ..memory import MemoryManager
    from ..messaging.bus import Bus
    from ..pool import Pool
    from ..stores.message_index import MessageIndex
    from .outbound import OutboundDispatcher


class HubRegistrationMixin:
    """Mixin providing registration and store-wiring methods for Hub."""

    if TYPE_CHECKING:
        adapter_registry: dict[tuple[Platform, str], ChannelAdapter]
        agent_registry: dict[str, AgentBase]
        bindings: dict[RoutingKey, Binding]
        inbound_bus: Bus[InboundMessage]
        outbound_dispatchers: dict[tuple[Platform, str], OutboundDispatcher]
        _alias_store: IdentityAliasStore | None
        _authenticators: dict[tuple[Platform, str], Authenticator]
        _memory: MemoryManager | None
        _memory_tasks: set
        _message_index: MessageIndex | None
        _platform_queue_maxsize: int
        _turn_store: TurnStore | None

        @property
        def pools(self) -> dict[str, Pool]: ...
        def set_debounce_ms(self, ms: int) -> None: ...  # noqa: ARG002  # justified: TYPE_CHECKING forward decl for real method on Hub
        def set_cancel_on_new_message(self, enabled: bool) -> None: ...  # noqa: ARG002  # justified: TYPE_CHECKING forward decl for real method on Hub

    def register_agent(self, agent: AgentBase) -> None:
        """Register an agent implementation by name."""
        self.agent_registry[agent.name] = agent
        if self._memory is not None and hasattr(agent, "_memory"):
            agent._memory = self._memory
        if hasattr(agent, "_task_registry"):
            agent._task_registry = self._memory_tasks
        router = getattr(agent, "command_router", None)
        if router is not None and hasattr(router, "_on_debounce_change"):
            router._on_debounce_change = self.set_debounce_ms
        if router is not None and hasattr(router, "_on_cancel_change"):
            router._on_cancel_change = self.set_cancel_on_new_message

    def set_memory(self, manager: MemoryManager) -> None:
        self._memory = manager
        for agent in self.agent_registry.values():
            if hasattr(agent, "_memory"):
                agent._memory = manager

    def set_turn_store(self, store: TurnStore) -> None:
        self._turn_store = store
        for pool in self.pools.values():
            pool._observer.register_turn_store(store)

    def set_message_index(self, store: MessageIndex) -> None:
        self._message_index = store
        for pool in self.pools.values():
            pool._observer.register_message_index(store)

    def set_alias_store(self, store: IdentityAliasStore) -> None:
        self._alias_store = store
        if self._memory is not None and hasattr(self._memory, "set_alias_store"):
            self._memory.set_alias_store(store)

    def register_adapter(
        self,
        platform: Platform,
        bot_id: str,
        adapter: ChannelAdapter,
    ) -> None:
        self.adapter_registry[(platform, bot_id)] = adapter
        self.inbound_bus.register(
            platform, maxsize=self._platform_queue_maxsize, bot_id=bot_id
        )

    def register_outbound_dispatcher(
        self,
        platform: Platform,
        bot_id: str,
        dispatcher: OutboundDispatcher,
    ) -> None:
        self.outbound_dispatchers[(platform, bot_id)] = dispatcher

    def register_authenticator(
        self, platform: Platform, bot_id: str, auth: Authenticator
    ) -> None:
        """Register the Authenticator for a (platform, bot_id) pair (C3)."""
        self._authenticators[(platform, bot_id)] = auth

    def _get_authenticator(
        self, platform: Platform, bot_id: str
    ) -> "Authenticator | None":
        return self._authenticators.get((platform, bot_id))

    def register_binding(
        self,
        platform: Platform,
        bot_id: str,
        scope_id: str,
        agent_name: str,
        pool_id: str,
    ) -> None:
        for ek, eb in self.bindings.items():
            if (
                ek.platform == platform
                and ek.bot_id == bot_id
                and ek.scope_id != scope_id
                and eb.pool_id == pool_id
            ):
                raise ValueError(
                    f"pool_id {pool_id!r} is already bound to scope_id "
                    f"{ek.scope_id!r} on {platform}:{bot_id}. "
                    "Each pool must serve at most one scope per (platform, bot_id)."
                )
        self.bindings[RoutingKey(platform, bot_id, scope_id)] = Binding(
            agent_name=agent_name,
            pool_id=pool_id,
        )
