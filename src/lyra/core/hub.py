from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .agent import AgentBase
from .audio_pipeline import AudioPipeline
from .hub_outbound import HubOutboundMixin
from .hub_protocol import (  # noqa: F401 — public re-export
    Binding,
    ChannelAdapter,
    RoutingKey,
)
from .hub_rate_limit import RateLimiter
from .inbound_bus import InboundBus
from .message import InboundAudio, InboundMessage, Platform
from .message_pipeline import (  # noqa: F401 — public re-export (Action)
    Action,
    MessagePipeline,
    PipelineResult,
)
from .pool import Pool
from .pool_manager import PoolManager

if TYPE_CHECKING:
    from collections import deque

    from ..stt import STTService
    from ..tts import TTSService
    from .circuit_breaker import CircuitRegistry
    from .cli_pool import CliPool
    from .memory import MemoryManager
    from .message_index import MessageIndex
    from .messages import MessageManager
    from .outbound_dispatcher import OutboundDispatcher
    from .pairing import PairingManager
    from .prefs_store import PrefsStore
    from .turn_store import TurnStore

log = logging.getLogger(__name__)


class Hub(HubOutboundMixin):
    """Central hub: InboundBus + OutboundDispatchers + adapter registry + pools."""

    # Class-level defaults used directly in tests and when Hub() is constructed
    # without config.  Production values come from [hub] in config.toml via
    # bootstrap.config._load_hub_config().
    BUS_SIZE = 100
    RATE_LIMIT = 20
    RATE_WINDOW = 60
    POOL_TTL: float = 604800.0  # 7 days

    # Class-level defaults for pool and bus config.
    MAX_SDK_HISTORY = 50                    # [pool] max_sdk_history
    SAFE_DISPATCH_TIMEOUT: float = 10.0    # [pool] safe_dispatch_timeout
    STAGING_MAXSIZE = 500                   # [inbound_bus] staging_maxsize
    PLATFORM_QUEUE_MAXSIZE = 100            # [inbound_bus] platform_queue_maxsize
    QUEUE_DEPTH_THRESHOLD = 100             # [inbound_bus] queue_depth_threshold
    MAX_MERGED_CHARS = 4096                 # [debouncer] max_merged_chars

    def __init__(  # noqa: PLR0913
        self,
        rate_limit: int = RATE_LIMIT,
        rate_window: int = RATE_WINDOW,
        pool_ttl: float = POOL_TTL,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        pairing_manager: "PairingManager | None" = None,
        stt: "STTService | None" = None,
        tts: "TTSService | None" = None,
        debounce_ms: int = 0,
        prefs_store: "PrefsStore | None" = None,
        turn_timeout: float | None = None,
        max_sdk_history: int = MAX_SDK_HISTORY,
        safe_dispatch_timeout: float = SAFE_DISPATCH_TIMEOUT,
        staging_maxsize: int = STAGING_MAXSIZE,
        platform_queue_maxsize: int = PLATFORM_QUEUE_MAXSIZE,
        queue_depth_threshold: int = QUEUE_DEPTH_THRESHOLD,
        max_merged_chars: int = MAX_MERGED_CHARS,
    ) -> None:
        self._platform_queue_maxsize = platform_queue_maxsize
        self.inbound_bus: InboundBus[InboundMessage] = InboundBus(
            name="inbound",
            staging_maxsize=staging_maxsize,
            queue_depth_threshold=queue_depth_threshold,
        )
        self.inbound_audio_bus: InboundBus[InboundAudio] = InboundBus(
            name="inbound-audio",
            staging_maxsize=staging_maxsize,
            queue_depth_threshold=queue_depth_threshold,
        )
        self.outbound_dispatchers: dict[tuple[Platform, str], OutboundDispatcher] = {}
        self.adapter_registry: dict[tuple[Platform, str], ChannelAdapter] = {}
        self.agent_registry: dict[str, AgentBase] = {}
        self.bindings: dict[RoutingKey, Binding] = {}
        self.circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._pairing_manager = pairing_manager
        self._message_index: MessageIndex | None = None
        self._stt: STTService | None = stt
        self._tts: TTSService | None = tts
        self._pool_ttl = pool_ttl
        self._debounce_ms = debounce_ms
        self._rate_limiter = RateLimiter(rate_limit, rate_window)
        self._start_time: float = time.monotonic()
        self._last_processed_at: float | None = None
        self._memory: MemoryManager | None = None
        self._memory_tasks: set[asyncio.Task] = set()
        self._turn_store: TurnStore | None = None
        self._turn_timeout = turn_timeout
        self._prefs_store: PrefsStore | None = prefs_store
        self._max_sdk_history = max_sdk_history
        self._safe_dispatch_timeout = safe_dispatch_timeout
        self._max_merged_chars = max_merged_chars
        self.cli_pool: CliPool | None = None
        self._pool_manager = PoolManager(self)
        self._audio_pipeline = AudioPipeline(self)

    @property
    def pools(self) -> dict[str, Pool]:
        return self._pool_manager.pools

    @property
    def bus(self) -> asyncio.Queue[InboundMessage]:
        return self.inbound_bus._staging

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

    def register_adapter(
        self,
        platform: Platform,
        bot_id: str,
        adapter: ChannelAdapter,
    ) -> None:
        self.adapter_registry[(platform, bot_id)] = adapter
        if platform not in self.inbound_bus.registered_platforms():
            self.inbound_bus.register(platform, maxsize=self._platform_queue_maxsize)
        if platform not in self.inbound_audio_bus.registered_platforms():
            self.inbound_audio_bus.register(
                platform, maxsize=self._platform_queue_maxsize
            )

    def register_outbound_dispatcher(
        self,
        platform: Platform,
        bot_id: str,
        dispatcher: OutboundDispatcher,
    ) -> None:
        self.outbound_dispatchers[(platform, bot_id)] = dispatcher

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

    def resolve_binding(self, msg: InboundMessage) -> Binding | None:
        """Resolve binding: exact key, then wildcard fallback, else None."""
        scope = msg.scope_id
        key = RoutingKey(Platform(msg.platform), msg.bot_id, scope)
        exact = self.bindings.get(key)
        if exact is not None:
            return exact
        wb = self.bindings.get(RoutingKey(Platform(msg.platform), msg.bot_id, "*"))
        if wb is not None:
            pid = RoutingKey(Platform(msg.platform), msg.bot_id, scope).to_pool_id()
            return Binding(agent_name=wb.agent_name, pool_id=pid)
        return None

    def get_or_create_pool(self, pool_id: str, agent_name: str) -> Pool:
        return self._pool_manager.get_or_create_pool(pool_id, agent_name)

    async def flush_pool(self, pool_id: str, reason: str = "end") -> None:
        await self._pool_manager.flush_pool(pool_id, reason)

    def set_debounce_ms(self, ms: int) -> None:
        self._pool_manager.set_debounce_ms(ms)

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

    async def _dispatch_pipeline_result(
        self,
        msg: InboundMessage,
        result: PipelineResult,
    ) -> None:
        """Dispatch a pipeline result: send command response or submit to pool."""
        if result.action == Action.COMMAND_HANDLED:
            if result.response and (result.response.content or result.response.audio):
                if result.response.audio:
                    try:
                        await self.dispatch_audio(msg, result.response.audio)
                    except Exception as exc:
                        log.exception("dispatch_audio() failed: %s", exc)
                if result.response.content:
                    try:
                        await self.dispatch_response(msg, result.response)
                    except Exception as exc:
                        log.exception("dispatch_response() failed: %s", exc)
            else:
                log.debug(
                    "command returned empty response for msg id=%s — skipping dispatch",
                    msg.id,
                )
        elif result.action == Action.SUBMIT_TO_POOL and result.pool:
            result.pool.submit(msg)

    async def run(self) -> None:
        """Hub bus consumer loop. Runs until cancelled."""
        pipeline = MessagePipeline(self)
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

    async def shutdown(self) -> None:
        """Flush all live pools, drain pending memory tasks, close memory DB."""
        for pool_id in list(self._pool_manager.pools.keys()):
            await self._pool_manager.flush_pool(pool_id, "shutdown")
        if self._memory_tasks:
            await asyncio.gather(*self._memory_tasks, return_exceptions=True)
        if self._memory is not None:
            await self._memory.close()
        if self._turn_store is not None:
            await self._turn_store.close()
        if self._message_index is not None:
            await self._message_index.close()
