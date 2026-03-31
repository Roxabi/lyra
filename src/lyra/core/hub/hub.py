from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import TYPE_CHECKING

from ..agent import AgentBase
from ..audio_pipeline import AudioPipeline
from ..authenticator import Authenticator
from ..bus import Bus
from ..identity import Identity
from ..inbound_bus import LocalBus
from ..message import InboundAudio, InboundMessage, Platform
from ..pool import Pool
from ..trust import TrustLevel
from .hub_outbound import HubOutboundMixin
from .hub_protocol import (  # noqa: F401 — public re-export
    Binding,
    ChannelAdapter,
    RoutingKey,
)
from .hub_rate_limit import RateLimiter
from .message_pipeline import (  # noqa: F401 — public re-export
    Action,
    PipelineResult,
)
from .middleware import build_default_pipeline
from .pool_manager import PoolManager

if TYPE_CHECKING:
    from collections import deque

    from ...stt import STTService
    from ...tts import TTSService
    from ..circuit_breaker import CircuitRegistry
    from ..cli_pool import CliPool
    from ..memory import MemoryManager
    from ..messages import MessageManager
    from ..stores.message_index import MessageIndex
    from ..stores.pairing import PairingManager
    from ..stores.prefs_store import PrefsStore
    from ..stores.turn_store import TurnStore
    from .event_bus import PipelineEventBus
    from .outbound_dispatcher import OutboundDispatcher

log = logging.getLogger(__name__)


class Hub(HubOutboundMixin):
    """Central hub: Bus + OutboundDispatchers + adapter registry + pools."""

    # Class-level defaults used directly in tests and when Hub() is constructed
    # without config.  Production values come from [hub] in config.toml via
    # bootstrap.config._load_hub_config().
    BUS_SIZE = 100
    RATE_LIMIT = 20
    RATE_WINDOW = 60
    POOL_TTL: float = 604800.0  # 7 days

    # Class-level defaults for pool and bus config.
    MAX_SDK_HISTORY = 50  # [pool] max_sdk_history
    SAFE_DISPATCH_TIMEOUT: float = 10.0  # [pool] safe_dispatch_timeout
    STAGING_MAXSIZE = 500  # [inbound_bus] staging_maxsize
    PLATFORM_QUEUE_MAXSIZE = 100  # [inbound_bus] platform_queue_maxsize
    QUEUE_DEPTH_THRESHOLD = 100  # [inbound_bus] queue_depth_threshold
    MAX_MERGED_CHARS = 4096  # [debouncer] max_merged_chars

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
        cancel_on_new_message: bool = False,
        prefs_store: "PrefsStore | None" = None,
        turn_timeout: float | None = None,
        max_sdk_history: int = MAX_SDK_HISTORY,
        safe_dispatch_timeout: float = SAFE_DISPATCH_TIMEOUT,
        staging_maxsize: int = STAGING_MAXSIZE,
        platform_queue_maxsize: int = PLATFORM_QUEUE_MAXSIZE,
        queue_depth_threshold: int = QUEUE_DEPTH_THRESHOLD,
        max_merged_chars: int = MAX_MERGED_CHARS,
        event_bus: "PipelineEventBus | None" = None,
    ) -> None:
        self._platform_queue_maxsize = platform_queue_maxsize
        self.inbound_bus: Bus[InboundMessage] = LocalBus(
            name="inbound",
            staging_maxsize=staging_maxsize,
            queue_depth_threshold=queue_depth_threshold,
        )
        self.inbound_audio_bus: Bus[InboundAudio] = LocalBus(
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
        self._cancel_on_new_message = cancel_on_new_message
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
        self._event_bus: PipelineEventBus | None = event_bus
        self._pool_manager = PoolManager(self)
        self._audio_pipeline = AudioPipeline(self)
        # C3: per-(platform, bot_id) authenticator registry — Hub is the trust authority
        self._authenticators: dict[tuple[Platform, str], Authenticator] = {}

    @property
    def pools(self) -> dict[str, Pool]:
        return self._pool_manager.pools

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

    def register_authenticator(
        self, platform: Platform, bot_id: str, auth: Authenticator
    ) -> None:
        """Register the Authenticator for a (platform, bot_id) pair.

        Called during bootstrap wiring. The Hub is the trust authority — adapters
        send raw identity fields; trust is resolved here (C3).
        """
        self._authenticators[(platform, bot_id)] = auth

    def _get_authenticator(
        self, platform: Platform, bot_id: str
    ) -> "Authenticator | None":
        """Return the registered Authenticator for (platform, bot_id), or None."""
        return self._authenticators.get((platform, bot_id))

    def resolve_identity(
        self, user_id: str | None, platform: str, bot_id: str
    ) -> Identity:
        """Resolve identity for a user on a given (platform, bot_id).

        Used by out-of-band adapter gates (e.g. Discord slash commands) that
        don't flow through the inbound message bus. Returns PUBLIC identity
        when no authenticator is registered for the pair.
        """
        try:
            key_platform = Platform(platform)
        except ValueError:
            return Identity(
                user_id=user_id or "", trust_level=TrustLevel.PUBLIC, is_admin=False
            )
        auth = self._get_authenticator(key_platform, bot_id)
        if auth is None:
            return Identity(
                user_id=user_id or "", trust_level=TrustLevel.PUBLIC, is_admin=False
            )
        return auth.resolve(user_id)

    def _resolve_message_trust(self, msg: InboundMessage) -> InboundMessage:
        """Re-resolve trust level on the Hub side (C3 — trust re-resolution).

        Adapters send messages with trust_level=PUBLIC (raw). Hub overwrites with
        the authoritative resolved identity before the pipeline sees the message.
        No-op when no authenticator is registered for the (platform, bot_id) pair.
        """
        try:
            key_platform = Platform(msg.platform)
        except ValueError:
            return msg
        auth = self._get_authenticator(key_platform, msg.bot_id)
        if auth is None:
            log.debug(
                "no authenticator for %s/%s — trust unchanged", key_platform, msg.bot_id
            )
            return msg
        uid = msg.user_id if msg.user_id else None
        roles = list(getattr(msg, "roles", ()))
        identity = auth.resolve(uid, roles=roles)
        trust_unchanged = identity.trust_level == msg.trust_level
        admin_unchanged = identity.is_admin == msg.is_admin
        if trust_unchanged and admin_unchanged:
            return msg
        return dataclasses.replace(
            msg, trust_level=identity.trust_level, is_admin=identity.is_admin
        )

    def _resolve_audio_trust(self, audio: "InboundAudio") -> "InboundAudio":
        """Re-resolve trust level for InboundAudio (C3).

        Mirrors _resolve_message_trust(). Called by AudioPipeline before the
        BLOCKED check to ensure Hub-resolved trust.
        """
        try:
            key_platform = Platform(audio.platform)
        except ValueError:
            return audio
        auth = self._get_authenticator(key_platform, audio.bot_id)
        if auth is None:
            log.debug(
                "no authenticator for %s/%s — trust unchanged",
                key_platform,
                audio.bot_id,
            )
            return audio
        uid = audio.user_id if audio.user_id else None
        identity = auth.resolve(uid)
        if identity.trust_level == audio.trust_level:
            return audio
        return dataclasses.replace(audio, trust_level=identity.trust_level)

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

    def set_cancel_on_new_message(self, enabled: bool) -> None:
        self._pool_manager.set_cancel_on_new_message(enabled)

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

    async def notify_shutdown_inflight(self, active_pool_ids: list[str]) -> None:
        """Notify users of in-flight requests that are about to be killed.

        Called just before cli_pool.stop() during graceful shutdown.
        Fire-and-forget with a 3s total timeout so it never blocks teardown.
        """
        from .outbound_errors import try_notify_user

        _RESTART_MSG = (
            "\u26a0\ufe0f I was restarted mid-response"
            " \u2014 please resend your message."
        )
        _NOTIFY_TIMEOUT = 3.0

        async def _notify_one(pool_id: str) -> None:
            pool = self.pools.get(pool_id)
            if pool is None or pool._last_msg is None:
                return
            msg = pool._last_msg
            platform_str = str(msg.platform)
            try:
                platform = Platform(platform_str)
            except ValueError:
                return
            adapter = self.adapter_registry.get((platform, msg.bot_id))
            if adapter is None:
                return
            circuit = (
                self.circuit_registry.get(platform_str)
                if self.circuit_registry is not None
                else None
            )
            await try_notify_user(
                platform_str, adapter, msg, _RESTART_MSG, circuit=circuit
            )

        tasks = [asyncio.create_task(_notify_one(pid)) for pid in active_pool_ids]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=_NOTIFY_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log.warning(
                    "notify_shutdown_inflight: timed out after %.1fs"
                    " (%d pools pending)",
                    _NOTIFY_TIMEOUT,
                    len(active_pool_ids),
                )

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
