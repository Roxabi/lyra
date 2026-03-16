from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from lyra.errors import ProviderError

from .agent import AgentBase
from .audio_pipeline import AudioPipeline
from .auth import TrustLevel
from .circuit_breaker import CircuitRegistry
from .context_resolver import ContextResolver
from .inbound_audio_bus import InboundAudioBus
from .inbound_bus import InboundBus
from .message import (
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
    Response,
)
from .message_pipeline import Action, MessagePipeline, PipelineResult
from .messages import MessageManager
from .outbound_dispatcher import OutboundDispatcher
from .pool import Pool
from .pool_manager import PoolManager

if TYPE_CHECKING:
    from ..stt import STTService
    from ..tts import TTSService
    from .memory import MemoryManager
    from .pairing import PairingManager
    from .prefs_store import PrefsStore
    from .turn_store import TurnStore

log = logging.getLogger(__name__)


class ChannelAdapter(Protocol):
    """Interface every channel adapter must implement.

    Security contract: adapters are responsible for verifying the identity
    of the sender (e.g. via platform token, signed webhook, or session)
    before constructing an InboundMessage. The hub trusts ``InboundMessage.user_id``
    as the authenticated sender identity (used for rate limiting and pairing) and
    ``InboundMessage.scope_id`` as the conversation scope (used for pool routing).
    Never derive either from unverified inbound data.
    """

    def normalize(self, raw: Any) -> InboundMessage: ...

    def normalize_audio(
        self, raw: Any, audio_bytes: bytes, mime_type: str, *, trust_level: TrustLevel
    ) -> InboundAudio: ...

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None: ...

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response to the channel with edit-in-place.

        When *outbound* is provided, adapters write the platform message ID
        to ``outbound.metadata["reply_message_id"]`` after sending the
        placeholder, mirroring the contract of :meth:`send`.
        """
        ...

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Send an outbound audio envelope (voice note) to the channel."""
        ...

    async def render_audio_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Stream outbound audio chunks to the channel.

        Adapters buffer chunks via ``buffer_audio_chunks()`` and send when
        complete. Mirrors ``send_streaming()`` for text.
        """
        ...

    async def render_voice_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Stream TTS audio to an active voice session (Discord voice channel).

        Adapters that support voice playback implement this method.
        """
        ...

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        """Send an outbound attachment (image/video/document/file) to the channel."""
        ...


class RoutingKey(NamedTuple):
    """Routing key: (platform, bot_id, scope_id). Use scope_id='*' for wildcard."""

    platform: Platform
    bot_id: str
    scope_id: str

    def to_pool_id(self) -> str:
        """Canonical pool ID: '{platform.value}:{bot_id}:{scope_id}'.

        Use this method as the single source of truth for pool ID format (ADR-001 §4).
        Never construct the pool ID string inline.
        """
        return f"{self.platform.value}:{self.bot_id}:{self.scope_id}"


@dataclass(frozen=True)
class Binding:
    agent_name: str
    pool_id: str


class Hub:
    """Central hub: InboundBus + OutboundDispatchers + adapter registry + pools."""

    BUS_SIZE = 100
    # Per-user sliding window: drop messages beyond this rate.
    RATE_LIMIT = 20  # max messages per user per window
    RATE_WINDOW = 60  # window size in seconds
    POOL_TTL: float = 3600.0  # evict idle pools after 1 hour (seconds)

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        bus_size: int = BUS_SIZE,
        rate_limit: int = RATE_LIMIT,
        rate_window: int = RATE_WINDOW,
        pool_ttl: float = POOL_TTL,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        pairing_manager: "PairingManager | None" = None,
        stt: "STTService | None" = None,
        tts: "TTSService | None" = None,
        debounce_ms: int = 0,
        context_resolver: ContextResolver | None = None,
        prefs_store: "PrefsStore | None" = None,
    ) -> None:
        self._bus_size = bus_size
        self.inbound_bus: InboundBus = InboundBus()
        self.inbound_audio_bus: InboundAudioBus = InboundAudioBus()
        self.outbound_dispatchers: dict[tuple[Platform, str], OutboundDispatcher] = {}
        self.adapter_registry: dict[tuple[Platform, str], ChannelAdapter] = {}
        self.agent_registry: dict[str, AgentBase] = {}
        self.bindings: dict[RoutingKey, Binding] = {}
        self.circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._pairing_manager = pairing_manager
        self._context_resolver = context_resolver
        self._stt: STTService | None = stt
        self._tts: TTSService | None = tts
        self._rate_limit = rate_limit
        self._rate_window = rate_window
        self._pool_ttl = pool_ttl
        self._debounce_ms = debounce_ms
        # Sliding window: maps (platform.value, bot_id, user_id) → deque of timestamps.
        # Rate limiting is per-user (not per-scope) to prevent rate-limit bypass
        # by switching chats. Entries are removed when the deque empties.
        self._rate_timestamps: dict[tuple[str, str, str], deque[float]] = {}
        # Health monitoring timestamps (SC-3, issue #111)
        self._start_time: float = time.monotonic()
        self._last_processed_at: float | None = None
        from .event_bus import EventBus, set_event_bus

        self._event_bus: EventBus = EventBus()
        set_event_bus(self._event_bus)
        # S2 — memory layer (issue #83)
        self._memory: "MemoryManager | None" = None
        self._memory_tasks: set[asyncio.Task] = set()
        # L1 — raw turn logging (issue #67)
        self._turn_store: "TurnStore | None" = None
        # S4 — user preference store (issue #42)
        self._prefs_store: "PrefsStore | None" = prefs_store
        # Extracted subsystems
        self._pool_manager = PoolManager(self)
        self._audio_pipeline = AudioPipeline(self)

    @property
    def pools(self) -> dict[str, Pool]:
        """Delegating property — PoolManager owns the dict."""
        return self._pool_manager.pools

    @property
    def bus(self) -> asyncio.Queue[InboundMessage]:
        """Backward-compat alias for the inbound staging queue.

        New code should use ``inbound_bus.put(platform, msg)`` for per-platform
        isolation. This property gives direct access to the staging queue and is
        retained for tests that inject messages without going through a platform queue.
        """
        return self.inbound_bus._staging

    # ------------------------------------------------------------------
    # Adapter registry
    # ------------------------------------------------------------------

    def register_agent(self, agent: AgentBase) -> None:
        """Register an agent implementation by name."""
        self.agent_registry[agent.name] = agent
        # S2/S4 — inject memory + wire task registry (issue #83)
        if self._memory is not None and hasattr(agent, "_memory"):
            agent._memory = self._memory
        if hasattr(agent, "_task_registry"):
            agent._task_registry = self._memory_tasks
        # Wire debounce_ms live-update callback if the agent has a command router.
        router = getattr(agent, "command_router", None)
        if router is not None and hasattr(router, "_on_debounce_change"):
            router._on_debounce_change = self.set_debounce_ms

    def set_memory(self, manager: "MemoryManager") -> None:
        """Set the MemoryManager and inject into all registered agents.

        Call this after constructing Hub and before processing messages.
        If called after register_agent(), already-registered agents are updated.
        """
        self._memory = manager
        for agent in self.agent_registry.values():
            if hasattr(agent, "_memory"):
                agent._memory = manager

    def set_turn_store(self, store: "TurnStore") -> None:
        """Set the TurnStore for L1 raw turn logging.

        Wire this after calling ``await store.connect()`` and before
        processing messages. Already-created pools are updated.
        """
        self._turn_store = store
        for pool in self.pools.values():
            pool._observer.register_turn_store(store)

    def register_adapter(
        self, platform: Platform, bot_id: str, adapter: ChannelAdapter
    ) -> None:
        """Register a channel adapter keyed by (platform, bot_id).

        Auto-registers the platform with the InboundBus if this is the first
        adapter for that platform. The adapter is responsible for authenticating
        inbound messages before placing them on the bus. See ChannelAdapter for
        the security contract.
        """
        self.adapter_registry[(platform, bot_id)] = adapter
        # Register per-platform inbound queue on first adapter for this platform
        if platform not in self.inbound_bus.registered_platforms():
            self.inbound_bus.register(platform, maxsize=self._bus_size)
        if platform not in self.inbound_audio_bus.registered_platforms():
            self.inbound_audio_bus.register(platform, maxsize=self._bus_size)

    def register_outbound_dispatcher(
        self, platform: Platform, bot_id: str, dispatcher: OutboundDispatcher
    ) -> None:
        """Register an OutboundDispatcher for the given (platform, bot_id).

        When registered, dispatch_response() and dispatch_streaming() route through
        the dispatcher queue instead of calling the adapter directly. The dispatcher
        owns the platform circuit breaker check.
        """
        self.outbound_dispatchers[(platform, bot_id)] = dispatcher

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def register_binding(
        self,
        platform: Platform,
        bot_id: str,
        scope_id: str,
        agent_name: str,
        pool_id: str,
    ) -> None:
        """Map (platform, bot_id, scope_id) -> (agent_name, pool_id).

        Raises ValueError if pool_id is already assigned to a different scope_id
        on the same (platform, bot_id) — each pool must serve at most one scope
        per (platform, bot_id) pair.
        """
        for existing_key, existing_binding in self.bindings.items():
            if (
                existing_key.platform == platform
                and existing_key.bot_id == bot_id
                and existing_key.scope_id != scope_id
                and existing_binding.pool_id == pool_id
            ):
                raise ValueError(
                    f"pool_id {pool_id!r} is already bound to scope_id "
                    f"{existing_key.scope_id!r} on {platform}:{bot_id}. "
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
        wildcard = RoutingKey(Platform(msg.platform), msg.bot_id, "*")
        wildcard_binding = self.bindings.get(wildcard)
        if wildcard_binding is not None:
            # Synthesise a per-scope pool_id from the message scope so each
            # conversation scope gets an isolated Pool (own lock, own subprocess,
            # own conversation).
            concrete_pool_id = RoutingKey(
                Platform(msg.platform), msg.bot_id, scope
            ).to_pool_id()
            return Binding(
                agent_name=wildcard_binding.agent_name, pool_id=concrete_pool_id
            )
        return None

    # ------------------------------------------------------------------
    # Pool delegation
    # ------------------------------------------------------------------

    def get_or_create_pool(self, pool_id: str, agent_name: str) -> Pool:
        """Delegate to PoolManager."""
        return self._pool_manager.get_or_create_pool(pool_id, agent_name)

    async def flush_pool(self, pool_id: str, reason: str = "end") -> None:
        """Delegate to PoolManager."""
        await self._pool_manager.flush_pool(pool_id, reason)

    def set_debounce_ms(self, ms: int) -> None:
        """Delegate to PoolManager."""
        self._pool_manager.set_debounce_ms(ms)

    # ------------------------------------------------------------------
    # PoolContext protocol implementation
    # ------------------------------------------------------------------

    def get_agent(self, name: str) -> AgentBase | None:
        """Return a registered agent by name, or None."""
        return self.agent_registry.get(name)

    def get_message(self, key: str) -> str | None:
        """Return a localised message by key, or None if no manager."""
        return self._msg_manager.get(key) if self._msg_manager else None

    def record_circuit_success(self) -> None:
        """Record a successful operation on all circuit breakers."""
        if self.circuit_registry is not None:
            for name in ("anthropic", "hub"):
                cb = self.circuit_registry.get(name)
                if cb is not None:
                    cb.record_success()

    def record_circuit_failure(self, exc: BaseException) -> None:
        """Record a failure on the hub CB; also on anthropic CB if ProviderError."""
        if self.circuit_registry is not None:
            _hub_cb = self.circuit_registry.get("hub")
            if _hub_cb is not None:
                _hub_cb.record_failure()
            if isinstance(exc, ProviderError):
                _ant_cb = self.circuit_registry.get("anthropic")
                if _ant_cb is not None:
                    _ant_cb.record_failure()

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _is_rate_limited_by_key(self, key: tuple[str, str, str]) -> bool:
        """Sliding-window rate check for an arbitrary (platform, bot_id, user_id) key.

        Returns True when the user has exceeded the per-window limit.
        Appends a timestamp and returns False otherwise.
        """
        now = time.monotonic()
        window_start = now - self._rate_window
        timestamps = self._rate_timestamps.get(key)
        if timestamps is not None:
            while timestamps and timestamps[0] < window_start:
                timestamps.popleft()
            if not timestamps:
                del self._rate_timestamps[key]
                timestamps = None
        if timestamps is not None and len(timestamps) >= self._rate_limit:
            return True
        if timestamps is None:
            timestamps = deque()
            self._rate_timestamps[key] = timestamps
        timestamps.append(now)
        return False

    def _is_rate_limited(self, msg: InboundMessage) -> bool:
        """Return True if this user has exceeded the per-window message limit."""
        # str() normalizes platform: InboundMessage.platform is str, not Platform enum
        key = (str(msg.platform), msg.bot_id, msg.user_id)
        return self._is_rate_limited_by_key(key)

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    async def circuit_breaker_drop(self, msg: InboundMessage) -> bool:
        """Return True if the circuit is open and a fast-fail reply was sent.

        Internal API — promoted from private for cross-module access by
        MessagePipeline. Not part of the public Hub contract.
        """
        if self.circuit_registry is None:
            return False
        cb = self.circuit_registry.get("anthropic")
        if cb is None or not cb.is_open():
            return False
        status = cb.get_status()
        retry_secs = int(status.retry_after or 0)
        _retry_str = str(retry_secs)
        _unavail = (
            self._msg_manager.get("unavailable", retry_secs=_retry_str)
            if self._msg_manager
            else f"Lyra is currently unavailable. Please try again in {retry_secs}s."
        )
        try:
            await self.dispatch_response(msg, Response(content=_unavail))
        except Exception as exc:
            log.exception("dispatch_response failed for fast-fail reply: %s", exc)
        return True

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch_response(
        self, msg: InboundMessage, response: Response | OutboundMessage
    ) -> None:
        """Send response back via the originating adapter.

        Routes through the OutboundDispatcher when one is registered for the
        platform (fire-and-forget queue). Falls back to a direct adapter call
        when no dispatcher is registered (used in tests and command responses).

        Accepts either a Response (backward compat) or OutboundMessage directly.
        """
        if isinstance(response, OutboundMessage):
            outbound = response
        else:
            outbound = response.to_outbound()
        if outbound.routing is None and msg.routing is not None:
            outbound.routing = msg.routing
        platform = Platform(msg.platform)
        dispatcher = self.outbound_dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            dispatcher.enqueue(msg, outbound)
            self._last_processed_at = time.monotonic()
        else:
            # Fallback: direct adapter call (backward compat / no dispatcher registered)
            adapter = self.adapter_registry.get((platform, msg.bot_id))
            if adapter is None:
                raise KeyError(
                    f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                    "Call register_adapter() before dispatching responses."
                )
            await adapter.send(msg, outbound)
            self._last_processed_at = time.monotonic()

        # /voice command: dispatch audio carried in Response.audio (pool path)
        if isinstance(response, Response) and response.audio:
            await self.dispatch_audio(msg, response.audio)

        # Voice modality: synthesize TTS audio in background after text is dispatched
        _should_speak = msg.modality == "voice" or (
            isinstance(response, Response) and response.speak
        )
        if _should_speak and self._tts is not None:
            text = outbound.to_text().strip()
            if text:
                task = asyncio.create_task(
                    self._audio_pipeline.synthesize_and_dispatch_audio(msg, text),
                    name=f"tts:{msg.id}",
                )
                self._memory_tasks.add(task)
                task.add_done_callback(self._memory_tasks.discard)

    async def dispatch_streaming(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response back via the originating adapter.

        Routes through the OutboundDispatcher when one is registered (fire-and-forget).
        Falls back to a direct adapter call when no dispatcher is registered.

        When *outbound* is provided it is forwarded to the adapter so the
        platform message ID can be recorded in ``outbound.metadata``.

        Voice modality: accumulates the full stream then delegates to
        dispatch_response() which handles both text and TTS dispatch.
        """
        # Voice modality: accumulate full stream, dispatch text + schedule TTS
        if msg.modality == "voice" and self._tts is not None:
            text_parts: list[str] = []
            async for chunk in chunks:
                text_parts.append(chunk)
            full_text = "".join(text_parts)
            if full_text.strip():
                await self.dispatch_response(msg, Response(content=full_text))
            return

        if (
            outbound is not None
            and outbound.routing is None
            and msg.routing is not None
        ):
            outbound.routing = msg.routing
        platform = Platform(msg.platform)
        dispatcher = self.outbound_dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            dispatcher.enqueue_streaming(msg, chunks, outbound)
            self._last_processed_at = time.monotonic()
            return
        # Fallback: direct adapter call (backward compat / no dispatcher registered)
        adapter = self.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching responses."
            )
        if hasattr(adapter, "send_streaming"):
            await adapter.send_streaming(msg, chunks, outbound)
        else:
            # Fallback: accumulate and send as one message
            if outbound is not None:
                log.warning(
                    "Adapter for %s lacks send_streaming; "
                    "reply_message_id will not be recorded",
                    msg.platform,
                )
            text = ""
            async for chunk in chunks:
                text += chunk
            await adapter.send(msg, OutboundMessage.from_text(text))
        self._last_processed_at = time.monotonic()

    async def dispatch_attachment(
        self, msg: InboundMessage, attachment: OutboundAttachment
    ) -> None:
        """Send an attachment back via the originating adapter.

        Routes through the OutboundDispatcher when one is registered (fire-and-forget).
        Falls back to a direct adapter call when no dispatcher is registered.
        """
        platform = Platform(msg.platform)
        dispatcher = self.outbound_dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            dispatcher.enqueue_attachment(msg, attachment)
            self._last_processed_at = time.monotonic()
            return
        # Fallback: direct adapter call (backward compat / no dispatcher registered)
        adapter = self.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching attachments."
            )
        await adapter.render_attachment(attachment, msg)
        self._last_processed_at = time.monotonic()

    async def dispatch_audio(self, msg: InboundMessage, audio: OutboundAudio) -> None:
        """Send an audio voice note back via the originating adapter.

        Routes through the OutboundDispatcher when one is registered (fire-and-forget).
        Falls back to a direct adapter call when no dispatcher is registered.
        """
        platform = Platform(msg.platform)
        dispatcher = self.outbound_dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            dispatcher.enqueue_audio(msg, audio)
            self._last_processed_at = time.monotonic()
            return
        # Fallback: direct adapter call (backward compat / no dispatcher registered)
        adapter = self.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching audio."
            )
        await adapter.render_audio(audio, msg)
        self._last_processed_at = time.monotonic()

    async def dispatch_audio_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[OutboundAudioChunk],
    ) -> None:
        """Stream audio chunks back via the originating adapter.

        Routes through the OutboundDispatcher when one is registered (fire-and-forget).
        Falls back to a direct adapter call when no dispatcher is registered.
        """
        platform = Platform(msg.platform)
        dispatcher = self.outbound_dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            dispatcher.enqueue_audio_stream(msg, chunks)
            self._last_processed_at = time.monotonic()
            return
        # Fallback: direct adapter call (backward compat / no dispatcher registered)
        adapter = self.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching audio stream."
            )
        await adapter.render_audio_stream(chunks, msg)
        self._last_processed_at = time.monotonic()

    async def dispatch_voice_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[OutboundAudioChunk],
    ) -> None:
        """Stream TTS audio to an active Discord voice session.

        Routes through the OutboundDispatcher when one is registered (fire-and-forget).
        Falls back to a direct adapter call when no dispatcher is registered.
        """
        platform = Platform(msg.platform)
        dispatcher = self.outbound_dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            dispatcher.enqueue_voice_stream(msg, chunks)
            self._last_processed_at = time.monotonic()
            return
        # Fallback: direct adapter call (backward compat / no dispatcher registered)
        adapter = self.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching voice stream."
            )
        await adapter.render_voice_stream(chunks, msg)
        self._last_processed_at = time.monotonic()

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def _dispatch_pipeline_result(
        self, msg: InboundMessage, result: PipelineResult
    ) -> None:
        """Dispatch a pipeline result: send command response or submit to pool."""
        if result.action == Action.COMMAND_HANDLED:
            if result.response and (result.response.content or result.response.audio):
                if result.response.audio:
                    try:
                        await self.dispatch_audio(
                            msg,
                            result.response.audio,
                        )
                    except Exception as exc:
                        log.exception(
                            "dispatch_audio() failed: %s",
                            exc,
                        )
                if result.response.content:
                    try:
                        await self.dispatch_response(
                            msg,
                            result.response,
                        )
                    except Exception as exc:
                        log.exception(
                            "dispatch_response() failed: %s",
                            exc,
                        )
            else:
                log.debug(
                    "command returned empty response for msg id=%s — skipping dispatch",
                    msg.id,
                )
        elif result.action == Action.SUBMIT_TO_POOL and result.pool:
            result.pool.submit(msg)

    async def run(self) -> None:
        """Hub bus consumer loop. Runs until cancelled."""
        from .event_bus import EventAggregator

        aggregator = EventAggregator(self._event_bus)
        agg_task = asyncio.create_task(aggregator.run(), name="event-aggregator")
        pipeline = MessagePipeline(self)
        try:
            while True:
                msg = await self.inbound_bus.get()
                try:
                    try:
                        result = await pipeline.process(msg)
                    except Exception:
                        log.exception(
                            "pipeline.process() failed for msg id=%s",
                            msg.id,
                        )
                        continue
                    await self._dispatch_pipeline_result(msg, result)
                finally:
                    self.inbound_bus.task_done()
        finally:
            agg_task.cancel()
            try:
                await agg_task
            except asyncio.CancelledError:
                pass

    async def shutdown(self) -> None:
        """Flush all live pools, drain pending memory tasks, close memory DB."""
        for pool_id in list(self._pool_manager.pools.keys()):
            await self._pool_manager.flush_pool(pool_id, "shutdown")
        # Drain pending memory tasks (extraction)
        if self._memory_tasks:
            await asyncio.gather(*self._memory_tasks, return_exceptions=True)
        if self._memory is not None:
            await self._memory.close()
        if self._turn_store is not None:
            await self._turn_store.close()
