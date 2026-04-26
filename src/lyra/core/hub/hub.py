from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ..auth.authenticator import Authenticator
from ..auth.identity import Identity
from ..config import HubConfig, PoolConfig
from ..messaging.bus import Bus
from ..messaging.inbound_bus import LocalBus
from ..messaging.message import InboundMessage, Platform
from ..pool import Pool
from ..tts_dispatch import AudioPipeline
from .hub_circuit_breaker import HubCircuitBreakerMixin
from .hub_dispatch import HubDispatchMixin
from .hub_pool_delegation import HubPoolDelegationMixin
from .hub_protocol import (  # noqa: F401 — public re-export
    Binding,
    ChannelAdapter,
    RoutingKey,
)
from .hub_rate_limit import RateLimiter
from .hub_registration import HubRegistrationMixin
from .hub_shutdown import HubShutdownMixin
from .identity_resolver import IdentityResolver
from .middleware import build_default_pipeline
from .outbound import OutboundRouter
from .pipeline import PoolManager

if TYPE_CHECKING:
    from collections import deque

    from lyra.infrastructure.stores.identity_alias_store import IdentityAliasStore
    from lyra.infrastructure.stores.message_index import MessageIndex
    from lyra.infrastructure.stores.pairing import PairingManager
    from lyra.infrastructure.stores.prefs_store import PrefsStore
    from lyra.infrastructure.stores.turn_store import TurnStore

    from ...stt import STTProtocol
    from ...tts import TtsProtocol
    from ..agent import AgentBase
    from ..circuit_breaker import CircuitRegistry
    from ..cli.cli_pool import CliPool
    from ..memory import MemoryManager
    from ..messaging.messages import MessageManager
    from .event_bus import PipelineEventBus
    from .outbound import OutboundDispatcher

log = logging.getLogger(__name__)


class Hub(
    HubShutdownMixin,
    HubCircuitBreakerMixin,
    HubPoolDelegationMixin,
    HubDispatchMixin,
    HubRegistrationMixin,
):
    """Central hub: Bus + OutboundDispatchers + adapter registry + pools."""

    BUS_SIZE = 100

    def __init__(  # noqa: PLR0913
        self,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        pairing_manager: "PairingManager | None" = None,
        stt: "STTProtocol | None" = None,
        tts: "TtsProtocol | None" = None,
        prefs_store: "PrefsStore | None" = None,
        event_bus: "PipelineEventBus | None" = None,
        inbound_bus: "Bus[InboundMessage] | None" = None,
        config: HubConfig | None = None,
    ) -> None:
        cfg = config if config is not None else HubConfig()
        if cfg.max_pools <= 0:
            raise ValueError(f"max_pools must be > 0, got {cfg.max_pools}")
        self._platform_queue_maxsize = cfg.platform_queue_maxsize
        self.inbound_bus: Bus[InboundMessage] = inbound_bus or LocalBus(
            name="inbound",
            staging_maxsize=cfg.staging_maxsize,
            queue_depth_threshold=cfg.queue_depth_threshold,
        )
        self.outbound_dispatchers: dict[tuple[Platform, str], OutboundDispatcher] = {}
        self.adapter_registry: dict[tuple[Platform, str], ChannelAdapter] = {}
        self.agent_registry: dict[str, AgentBase] = {}
        self.bindings: dict[RoutingKey, Binding] = {}
        self.circuit_registry: CircuitRegistry | None = circuit_registry
        self._msg_manager: MessageManager | None = msg_manager
        self._pairing_manager = pairing_manager
        self._message_index: MessageIndex | None = None
        self._stt: STTProtocol | None = stt
        self._tts_value: TtsProtocol | None = tts
        self._pool_ttl = cfg.pool_ttl
        self._debounce_ms = cfg.debounce_ms
        self._cancel_on_new_message = cfg.cancel_on_new_message
        self._rate_limiter = RateLimiter(cfg.rate_limit, cfg.rate_window)
        self._start_time: float = time.monotonic()
        self._memory: MemoryManager | None = None
        self._memory_tasks: set[asyncio.Task] = set()
        self._turn_store: TurnStore | None = None
        self._turn_timeout = cfg.turn_timeout
        self._prefs_store: PrefsStore | None = prefs_store
        self._safe_dispatch_timeout = cfg.safe_dispatch_timeout
        self._max_merged_chars = cfg.max_merged_chars
        self._max_pools = cfg.max_pools
        self.cli_pool: CliPool | None = None
        self._event_bus: PipelineEventBus | None = event_bus
        self._pool_config = PoolConfig(
            turn_timeout=cfg.turn_timeout,
            debounce_ms=cfg.debounce_ms,
            safe_dispatch_timeout=cfg.safe_dispatch_timeout,
            max_merged_chars=cfg.max_merged_chars,
            cancel_on_new_message=cfg.cancel_on_new_message,
        )
        self._pool_manager = PoolManager(self, self._pool_config)
        self._audio_pipeline = AudioPipeline(self)
        self._authenticators: dict[tuple[Platform, str], Authenticator] = {}
        self._alias_store: IdentityAliasStore | None = None
        self._identity_resolver = IdentityResolver(
            authenticators=self._authenticators,
            bindings=self.bindings,
        )
        self._outbound_router = OutboundRouter(
            adapters=self.adapter_registry,
            dispatchers=self.outbound_dispatchers,
            audio_pipeline=self._audio_pipeline,
            circuit_registry=self.circuit_registry,
            msg_manager=self._msg_manager,
            tts=self._tts,
            memory_tasks=self._memory_tasks,
        )

    @property
    def _last_processed_at(self) -> float | None:
        return self._outbound_router.last_processed_at

    @property
    def _tts(self) -> "TtsProtocol | None":
        return self._tts_value

    @_tts.setter
    def _tts(self, value: "TtsProtocol | None") -> None:
        self._tts_value = value
        self._outbound_router.set_tts(value)

    @property
    def pools(self) -> dict[str, Pool]:
        return self._pool_manager.pools

    def resolve_identity(
        self, user_id: str | None, platform: str, bot_id: str
    ) -> Identity:
        """Resolve identity for a user on a given (platform, bot_id)."""
        return self._identity_resolver.resolve_identity(user_id, platform, bot_id)

    def _resolve_message_trust(self, msg: InboundMessage) -> InboundMessage:
        """Re-resolve trust level on the Hub side (C3 — trust re-resolution)."""
        return self._identity_resolver.resolve_message_trust(msg)

    def resolve_binding(self, msg: InboundMessage) -> Binding | None:
        """Resolve binding: exact key, then wildcard fallback, else None."""
        return self._identity_resolver.resolve_binding(msg)

    def get_agent(self, name: str) -> AgentBase | None:
        return self.agent_registry.get(name)

    def get_message(self, key: str) -> str | None:
        return self._msg_manager.get(key) if self._msg_manager else None

    @property
    def _rate_timestamps(self) -> dict[tuple[str, str, str], deque[float]]:
        return self._rate_limiter._timestamps

    @property
    def _rate_limit(self) -> int:
        return self._rate_limiter._limit

    @property
    def _rate_window(self) -> int:
        return self._rate_limiter._window

    def _is_rate_limited_by_key(self, key: tuple[str, str, str]) -> bool:
        return self._rate_limiter.is_limited_by_key(key)

    def _is_rate_limited(self, msg: InboundMessage) -> bool:
        return self._rate_limiter.is_limited(msg)

    async def run(self) -> None:
        """Hub bus consumer loop. Runs until cancelled."""
        pipeline = build_default_pipeline(self, event_bus=self._event_bus)
        while True:
            msg = await self.inbound_bus.get()
            try:
                try:
                    result = await pipeline.process(msg)
                except Exception:
                    log.exception("pipeline.process() failed for msg id=%s", msg.id)
                    continue
                await self._dispatch_pipeline_result(msg, result)
            finally:
                self.inbound_bus.task_done()
