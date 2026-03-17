from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from lyra.errors import ProviderError

from .agent import AgentBase
from .audio_pipeline import AudioPipeline
from .hub_protocol import (  # noqa: F401 — public re-export
    Binding,
    ChannelAdapter,
    RoutingKey,
)
from .hub_rate_limit import RateLimiter
from .inbound_bus import InboundBus
from .message import InboundAudio, InboundMessage, OutboundMessage, Platform, Response
from .message_pipeline import (  # noqa: F401 — public re-export (Action)
    Action,
    MessagePipeline,
    PipelineResult,
)
from .pool import Pool
from .pool_manager import PoolManager

if TYPE_CHECKING:
    from collections import deque
    from collections.abc import AsyncIterator

    from ..stt import STTService
    from ..tts import TTSService
    from .circuit_breaker import CircuitRegistry
    from .context_resolver import ContextResolver
    from .memory import MemoryManager
    from .message import OutboundAttachment, OutboundAudio, OutboundAudioChunk
    from .messages import MessageManager
    from .outbound_dispatcher import OutboundDispatcher
    from .pairing import PairingManager
    from .prefs_store import PrefsStore
    from .turn_store import TurnStore

log = logging.getLogger(__name__)


class Hub:
    """Central hub: InboundBus + OutboundDispatchers + adapter registry + pools."""

    BUS_SIZE = 100
    RATE_LIMIT = 20
    RATE_WINDOW = 60
    POOL_TTL: float = 3600.0

    def __init__(  # noqa: PLR0913
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
        self.inbound_bus: InboundBus[InboundMessage] = InboundBus(name="inbound")
        self.inbound_audio_bus: InboundBus[InboundAudio] = InboundBus(
            name="inbound-audio"
        )
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
        self._pool_ttl = pool_ttl
        self._debounce_ms = debounce_ms
        self._rate_limiter = RateLimiter(rate_limit, rate_window)
        self._start_time: float = time.monotonic()
        self._last_processed_at: float | None = None
        self._memory: MemoryManager | None = None
        self._memory_tasks: set[asyncio.Task] = set()
        self._turn_store: TurnStore | None = None
        self._prefs_store: PrefsStore | None = prefs_store
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

    def register_adapter(
        self,
        platform: Platform,
        bot_id: str,
        adapter: ChannelAdapter,
    ) -> None:
        self.adapter_registry[(platform, bot_id)] = adapter
        if platform not in self.inbound_bus.registered_platforms():
            self.inbound_bus.register(platform, maxsize=self._bus_size)
        if platform not in self.inbound_audio_bus.registered_platforms():
            self.inbound_audio_bus.register(platform, maxsize=self._bus_size)

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

    def record_circuit_success(self) -> None:
        if self.circuit_registry is not None:
            for name in ("anthropic", "hub"):
                cb = self.circuit_registry.get(name)
                if cb is not None:
                    cb.record_success()

    def record_circuit_failure(self, exc: BaseException) -> None:
        if self.circuit_registry is not None:
            _hub_cb = self.circuit_registry.get("hub")
            if _hub_cb is not None:
                _hub_cb.record_failure()
            if isinstance(exc, ProviderError):
                _ant_cb = self.circuit_registry.get("anthropic")
                if _ant_cb is not None:
                    _ant_cb.record_failure()

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

    # ------------------------------------------------------------------
    # Outbound routing
    # ------------------------------------------------------------------

    async def _route_outbound(
        self,
        msg: InboundMessage,
        enqueue_fn: Callable[[OutboundDispatcher], None],
        fallback_fn: Callable[[ChannelAdapter], Coroutine[Any, Any, None]],
        *,
        resource: str = "response",
    ) -> None:
        """Core outbound routing: dispatcher queue -> direct adapter fallback.

        Checks whether an OutboundDispatcher is registered for the message's
        (platform, bot_id). If so, calls *enqueue_fn* (fire-and-forget queue).
        Otherwise resolves the adapter and calls *fallback_fn* (direct send).
        Updates ``_last_processed_at`` in both branches.

        Args:
            msg: The inbound message that triggered this outbound dispatch.
            enqueue_fn: Called with the dispatcher when one is registered.
            fallback_fn: Async callable invoked with the adapter as fallback.
            resource: Label used in the KeyError message when no adapter exists.
        """
        platform = Platform(msg.platform)
        dispatcher = self.outbound_dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            enqueue_fn(dispatcher)
            self._last_processed_at = time.monotonic()
            return
        adapter = self.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                f"Call register_adapter() before dispatching {resource}."
            )
        await fallback_fn(adapter)
        self._last_processed_at = time.monotonic()

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

    async def dispatch_response(
        self,
        msg: InboundMessage,
        response: Response | OutboundMessage,
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
            # Forward dispatch callback so deferred turn logging can capture
            # reply_message_id after the adapter sends (#316).
            _cb = response.metadata.get("_on_dispatched")
            if _cb is not None:
                outbound.metadata["_on_dispatched"] = _cb
        if outbound.routing is None and msg.routing is not None:
            outbound.routing = msg.routing

        async def _fallback_and_notify(adapter: ChannelAdapter) -> None:
            await adapter.send(msg, outbound)
            _dispatched = outbound.metadata.pop("_on_dispatched", None)
            if callable(_dispatched):
                _dispatched(outbound)

        await self._route_outbound(
            msg,
            enqueue_fn=lambda d: d.enqueue(msg, outbound),
            fallback_fn=_fallback_and_notify,
            resource="responses",
        )

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

    async def dispatch_streaming(  # noqa: C901 — streaming protocol: voice/dispatcher/fallback branches + callback
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
            # Invoke _on_dispatched so the turn is logged even on voice path (#316).
            if outbound is not None:
                _dispatched = outbound.metadata.pop("_on_dispatched", None)
                if callable(_dispatched):
                    _dispatched(outbound)
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
        # Invoke dispatch callback for fallback (non-queued) path (#316).
        if outbound is not None:
            _dispatched = outbound.metadata.pop("_on_dispatched", None)
            if callable(_dispatched):
                _dispatched(outbound)
        self._last_processed_at = time.monotonic()

    async def dispatch_attachment(
        self,
        msg: InboundMessage,
        attachment: OutboundAttachment,
    ) -> None:
        """Send an attachment back via the originating adapter."""
        await self._route_outbound(
            msg,
            enqueue_fn=lambda d: d.enqueue_attachment(msg, attachment),
            fallback_fn=lambda a: a.render_attachment(attachment, msg),
            resource="attachments",
        )

    async def dispatch_audio(
        self,
        msg: InboundMessage,
        audio: OutboundAudio,
    ) -> None:
        """Send an audio voice note back via the originating adapter."""
        await self._route_outbound(
            msg,
            enqueue_fn=lambda d: d.enqueue_audio(msg, audio),
            fallback_fn=lambda a: a.render_audio(audio, msg),
            resource="audio",
        )

    async def dispatch_audio_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[OutboundAudioChunk],
    ) -> None:
        """Stream audio chunks back via the originating adapter."""
        await self._route_outbound(
            msg,
            enqueue_fn=lambda d: d.enqueue_audio_stream(msg, chunks),
            fallback_fn=lambda a: a.render_audio_stream(chunks, msg),
            resource="audio stream",
        )

    async def dispatch_voice_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[OutboundAudioChunk],
    ) -> None:
        """Stream TTS audio to an active Discord voice session."""
        await self._route_outbound(
            msg,
            enqueue_fn=lambda d: d.enqueue_voice_stream(msg, chunks),
            fallback_fn=lambda a: a.render_voice_stream(chunks, msg),
            resource="voice stream",
        )

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
