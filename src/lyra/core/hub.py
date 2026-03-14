from __future__ import annotations

import asyncio
import dataclasses
import enum
import logging
import os
import tempfile
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from lyra.errors import ProviderError

from .agent import AgentBase
from .circuit_breaker import CircuitRegistry
from .command_parser import CommandParser
from .inbound_audio_bus import InboundAudioBus
from .inbound_bus import InboundBus
from .message import (
    GENERIC_ERROR_REPLY,
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
    Response,
)
from .messages import MessageManager
from .outbound_dispatcher import OutboundDispatcher
from .pool import Pool

if TYPE_CHECKING:
    from ..stt import STTService
    from .context_resolver import ContextResolver
    from .pairing import PairingManager

log = logging.getLogger(__name__)

_command_parser = CommandParser()


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
        self, raw: Any, audio_bytes: bytes, mime_type: str
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


class Action(enum.Enum):
    """Terminal action for the message pipeline."""

    DROP = "drop"
    COMMAND_HANDLED = "command_handled"
    SUBMIT_TO_POOL = "submit_to_pool"


@dataclass(frozen=True)
class PipelineResult:
    """Immutable result from MessagePipeline.process()."""

    action: Action
    response: Response | None = None
    pool: Pool | None = None


_DROP = PipelineResult(action=Action.DROP)


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
        pairing_manager: PairingManager | None = None,
        stt: STTService | None = None,
        debounce_ms: int = 0,
        context_resolver: ContextResolver | None = None,
    ) -> None:
        self._bus_size = bus_size
        self.inbound_bus: InboundBus = InboundBus()
        self.inbound_audio_bus: InboundAudioBus = InboundAudioBus()
        self.outbound_dispatchers: dict[tuple[Platform, str], OutboundDispatcher] = {}
        self.adapter_registry: dict[tuple[Platform, str], ChannelAdapter] = {}
        self.agent_registry: dict[str, AgentBase] = {}
        self.bindings: dict[RoutingKey, Binding] = {}
        self.pools: dict[str, Pool] = {}
        self.circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._pairing_manager = pairing_manager
        self._context_resolver = context_resolver
        self._stt: STTService | None = stt
        self._rate_limit = rate_limit
        self._rate_window = rate_window
        self._pool_ttl = pool_ttl
        self._debounce_ms = debounce_ms
        self._last_eviction_check: float = 0.0
        # Sliding window: maps (platform.value, bot_id, user_id) → deque of timestamps.
        # Rate limiting is per-user (not per-scope) to prevent rate-limit bypass
        # by switching chats. Entries are removed when the deque empties.
        self._rate_timestamps: dict[tuple[str, str, str], deque[float]] = {}
        # Health monitoring timestamps (SC-3, issue #111)
        self._start_time: float = time.monotonic()
        self._last_processed_at: float | None = None

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
        # Wire debounce_ms live-update callback if the agent has a command router.
        router = getattr(agent, "command_router", None)
        if router is not None and hasattr(router, "_on_debounce_change"):
            router._on_debounce_change = self.set_debounce_ms

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
    # Pools
    # ------------------------------------------------------------------

    def get_or_create_pool(self, pool_id: str, agent_name: str) -> Pool:
        """Return existing pool or create a new one.

        Lazily evicts idle pools that have exceeded the TTL on each call
        to bound memory growth.
        """
        self._evict_stale_pools()
        if pool_id not in self.pools:
            self.pools[pool_id] = Pool(
                pool_id=pool_id,
                agent_name=agent_name,
                ctx=self,
                debounce_ms=self._debounce_ms,
            )
        pool = self.pools[pool_id]
        pool._touch()
        return pool

    def set_debounce_ms(self, ms: int) -> None:
        """Update debounce window on all live pools and future pools."""
        self._debounce_ms = ms
        for pool in self.pools.values():
            pool.debounce_ms = ms

    def _evict_stale_pools(self) -> None:
        """Remove idle pools whose last activity exceeds the TTL.

        Throttled: skips the scan if less than TTL/10 has elapsed since the
        last check, turning the common case (nothing to evict) into a single
        float comparison.
        """
        now = time.monotonic()
        if (now - self._last_eviction_check) < self._pool_ttl / 10:
            return
        self._last_eviction_check = now
        stale = [
            pid
            for pid, pool in self.pools.items()
            if pool.is_idle and (now - pool.last_active) > self._pool_ttl
        ]
        for pid in stale:
            del self.pools[pid]
        if stale:
            log.info("evicted %d stale pool(s)", len(stale))
            log.debug("evicted pool IDs: %s", stale)

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

    def _is_rate_limited(self, msg: InboundMessage) -> bool:
        """Return True if this user has exceeded the per-window message limit.

        Uses a sliding window: tracks timestamps of recent messages and drops
        any that arrive after RATE_LIMIT messages within RATE_WINDOW seconds.
        Inactive-user entries are cleaned up when their deque empties to prevent
        unbounded dict growth.
        """
        # str() normalizes platform: InboundMessage.platform is str, not Platform enum
        key = (str(msg.platform), msg.bot_id, msg.user_id)
        now = time.monotonic()
        window_start = now - self._rate_window
        timestamps = self._rate_timestamps.get(key)
        if timestamps is not None:
            # Evict timestamps outside the current window
            while timestamps and timestamps[0] < window_start:
                timestamps.popleft()
            # Empty deque → user has been inactive; clean up to bound dict size
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
            return
        # Fallback: direct adapter call (backward compat / no dispatcher registered)
        adapter = self.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching responses."
            )
        await adapter.send(msg, outbound)
        self._last_processed_at = time.monotonic()

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
        """
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

        Note: fallback path has no circuit breaker coverage; prefer a registered
        dispatcher for production audio streaming.
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

    # ------------------------------------------------------------------
    # Audio consumer loop
    # ------------------------------------------------------------------

    async def _process_audio_item(self, audio: InboundAudio) -> None:
        from ..stt import is_whisper_noise

        try:
            platform_enum = Platform(audio.platform)
        except ValueError:
            log.warning(
                "unknown platform %r in audio id=%s — audio dropped",
                audio.platform,
                audio.id,
            )
            return
        key = RoutingKey(platform_enum, audio.bot_id, audio.scope_id)

        if audio.trust != "user":
            log.error(
                "audio %s has trust=%r (expected 'user') — dropped",
                audio.id,
                audio.trust,
            )
            return

        if self._stt is None:
            _content = (
                self._msg_manager.get("stt_unsupported")
                if self._msg_manager
                else "Voice messages are not supported — STT is not configured."
            )
            await self._dispatch_audio_reply(audio, _content)
            log.warning("STT not configured — audio %s dropped", audio.id)
            return

        tmp = Path(
            await asyncio.to_thread(
                self._write_temp_audio,
                audio.audio_bytes,
                _mime_to_ext(audio.mime_type),
            )
        )
        try:
            result = await self._stt.transcribe(tmp)
        finally:
            tmp.unlink(missing_ok=True)

        if is_whisper_noise(result.text):
            _content = (
                self._msg_manager.get("stt_noise")
                if self._msg_manager
                else "I couldn't make out your voice message, please try again."
            )
            await self._dispatch_audio_reply(audio, _content)
            log.info("STT noise for audio %s — replied with stt_noise", audio.id)
            return

        text = f"\U0001f3a4 [voice]: {result.text}"
        msg = InboundMessage(
            id=audio.id,
            platform=audio.platform,
            bot_id=audio.bot_id,
            scope_id=audio.scope_id,
            user_id=audio.user_id,
            user_name=audio.user_name,
            is_mention=audio.is_mention,
            text=result.text,
            text_raw=text,
            timestamp=audio.timestamp,
            trust_level=audio.trust_level,
            trust=audio.trust,
            platform_meta=audio.platform_meta,
            routing=audio.routing,
        )
        try:
            self.inbound_bus.put(platform_enum, msg)
        except asyncio.QueueFull:
            log.warning("inbound bus full — transcribed audio %s dropped", audio.id)
            return
        log.info(
            "Audio %s transcribed (%s, %.1fs) → re-enqueued as text on %s",
            audio.id,
            result.language,
            result.duration_seconds,
            key,
        )

    async def _audio_loop(self) -> None:
        """Drain InboundAudioBus, transcribe via STT, re-enqueue as InboundMessage.

        When STT is not configured, sends an ``stt_unsupported`` reply and drops
        the audio envelope. When transcription fails, sends an ``stt_failed``
        reply. When the transcription is noise, sends an ``stt_noise`` reply.

        Runs until cancelled.
        """
        while True:
            audio: InboundAudio = await self.inbound_audio_bus.get()
            try:
                await self._process_audio_item(audio)
            except Exception:
                log.exception("audio_loop failed for audio id=%s", audio.id)
                _content = (
                    self._msg_manager.get("stt_failed")
                    if self._msg_manager
                    else "Sorry, I couldn't transcribe your voice message."
                )
                try:
                    await self._dispatch_audio_reply(audio, _content)
                except Exception:
                    log.exception(
                        "dispatch_audio_reply failed for audio id=%s", audio.id
                    )
            finally:
                self.inbound_audio_bus.task_done()

    @staticmethod
    def _write_temp_audio(data: bytes, suffix: str) -> str:
        """Write *data* to a temp file (blocking I/O, run via to_thread)."""
        fd, path = tempfile.mkstemp(suffix=suffix)
        try:
            os.write(fd, data)
        except BaseException:
            os.close(fd)
            Path(path).unlink(missing_ok=True)
            raise
        os.close(fd)
        return path

    async def _dispatch_audio_reply(self, audio: InboundAudio, content: str) -> None:
        """Send an error/info reply for an audio envelope.

        Constructs a synthetic InboundMessage from the audio envelope so
        dispatch_response() can route the reply back to the originating adapter.
        """
        synthetic = InboundMessage(
            id=audio.id,
            platform=audio.platform,
            bot_id=audio.bot_id,
            scope_id=audio.scope_id,
            user_id=audio.user_id,
            user_name=audio.user_name,
            is_mention=False,
            text="",
            text_raw="",
            timestamp=audio.timestamp,
            trust_level=audio.trust_level,
            trust=audio.trust,
            platform_meta=audio.platform_meta,
            routing=audio.routing,
        )
        await self.dispatch_response(synthetic, Response(content=content))

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def _circuit_breaker_drop(self, msg: InboundMessage) -> bool:
        """Return True if the circuit is open and a fast-fail reply was sent."""
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

    async def run(self) -> None:
        """Hub bus consumer loop. Runs until cancelled."""
        pipeline = MessagePipeline(self)
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
                if result.action == Action.COMMAND_HANDLED:
                    if result.response and (
                        result.response.content or result.response.audio
                    ):
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
                            "command returned empty response"
                            " for msg id=%s — skipping dispatch",
                            msg.id,
                        )
                elif result.action == Action.SUBMIT_TO_POOL and result.pool:
                    result.pool.submit(msg)
            finally:
                self.inbound_bus.task_done()


class MessagePipeline:
    """Fail-fast message routing pipeline extracted from Hub.run().

    Each guard stage returns ``PipelineResult`` to stop processing or
    ``None`` to continue to the next stage. Terminal stages always
    return a ``PipelineResult``.
    """

    def __init__(self, hub: Hub) -> None:
        self._hub = hub

    async def process(
        self,
        msg: InboundMessage,
    ) -> PipelineResult:
        """Route *msg* through the pipeline stages."""
        result = self._validate_platform(msg)
        if result is not None:
            return result

        key = RoutingKey(
            Platform(msg.platform),
            msg.bot_id,
            msg.scope_id,
        )

        result = self._check_rate_limit(msg, key)
        if result is not None:
            return result

        binding = self._resolve_binding(msg, key)
        if binding is None:
            return _DROP

        agent = self._lookup_agent(binding, key)
        if agent is None:
            return _DROP

        pool = self._hub.get_or_create_pool(
            binding.pool_id,
            binding.agent_name,
        )
        router = getattr(agent, "command_router", None)

        # Parse command prefix and attach CommandContext to the message
        cmd_ctx = _command_parser.parse(msg.text)
        if cmd_ctx is not None:
            msg = dataclasses.replace(msg, command=cmd_ctx)

        if router and router.is_command(msg):
            return await self._dispatch_command(
                msg,
                router,
                pool,
                key,
            )

        return await self._submit_to_pool(msg, pool, key)

    # -- guard stages (return None to continue) ---

    def _validate_platform(
        self,
        msg: InboundMessage,
    ) -> PipelineResult | None:
        try:
            Platform(msg.platform)
        except ValueError:
            log.warning(
                "unknown platform %r in msg id=%s — message dropped",
                msg.platform,
                msg.id,
            )
            return _DROP
        return None

    def _check_rate_limit(
        self,
        msg: InboundMessage,
        key: RoutingKey,
    ) -> PipelineResult | None:
        if self._hub._is_rate_limited(msg):
            log.warning(
                "rate limit exceeded for %s — message dropped",
                key,
            )
            return _DROP
        return None

    def _resolve_binding(
        self,
        msg: InboundMessage,
        key: RoutingKey,
    ) -> Binding | None:
        """Return resolved binding, or None (with log) to drop."""
        binding = self._hub.resolve_binding(msg)
        if binding is None:
            log.warning(
                "unmatched routing key %s — message dropped",
                key,
            )
        return binding

    def _lookup_agent(
        self,
        binding: Binding,
        key: RoutingKey,
    ) -> AgentBase | None:
        """Return agent, or None (with log) to drop."""
        agent = self._hub.agent_registry.get(binding.agent_name)
        if agent is None:
            log.warning(
                "no agent registered for %r (routing %s) — message dropped",
                binding.agent_name,
                key,
            )
        return agent

    # -- terminal stages ---

    async def _dispatch_command(
        self,
        msg: InboundMessage,
        router: Any,
        pool: Pool,
        key: RoutingKey,
    ) -> PipelineResult:
        try:
            response = await router.dispatch(msg, pool)
        except Exception as exc:
            log.exception(
                "command dispatch failed for %s: %s",
                key,
                exc,
            )
            _content = (
                self._hub._msg_manager.get("generic")
                if self._hub._msg_manager
                else GENERIC_ERROR_REPLY
            )
            response = Response(content=_content)
            return PipelineResult(action=Action.COMMAND_HANDLED, response=response)

        if response is None:
            # !-prefixed command not found — fall through to pool submission
            return await self._submit_to_pool(msg, pool, key)

        return PipelineResult(
            action=Action.COMMAND_HANDLED,
            response=response,
        )

    async def _resolve_context(
        self, msg: InboundMessage, pool: Pool, pool_id: str
    ) -> None:
        """Attempt reply-to-resume before pool.submit(). No-op on any failure."""
        if msg.reply_to_id is None:
            return
        resolver = self._hub._context_resolver
        if resolver is None:
            return
        resolved = await resolver.resolve(msg.reply_to_id)
        if resolved is None:
            return
        if resolved.pool_id != pool_id:
            log.info(
                "reply-to-resume: cross-pool mismatch"
                " (resolved=%r current=%r) — skipping",
                resolved.pool_id,
                pool_id,
            )
            return
        if not pool.is_idle:
            log.info(
                "reply-to-resume: pool %r busy — skipping resume of session %r",
                pool_id,
                resolved.session_id,
            )
            return
        log.info(
            "reply-to-resume: resuming session %r for pool %r",
            resolved.session_id,
            pool_id,
        )
        await pool.resume_session(resolved.session_id)

    async def _submit_to_pool(
        self,
        msg: InboundMessage,
        pool: Pool,
        key: RoutingKey,
    ) -> PipelineResult:
        if (key.platform, msg.bot_id) not in self._hub.adapter_registry:
            log.error(
                "no adapter registered for (%s, %s) — response dropped",
                msg.platform,
                msg.bot_id,
            )
            return _DROP
        if await self._hub._circuit_breaker_drop(msg):
            return _DROP
        await self._resolve_context(msg, pool, pool.pool_id)
        return PipelineResult(
            action=Action.SUBMIT_TO_POOL,
            pool=pool,
        )


# ---------------------------------------------------------------------------
# Module-level helpers for the pairing gate
# ---------------------------------------------------------------------------


def _mime_to_ext(mime_type: str) -> str:
    """Map common audio MIME types to file extensions for STT temp files."""
    _MAP = {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
    }
    return _MAP.get(mime_type, ".ogg")



