from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from lyra.errors import ProviderError

from ..agent import AgentBase
from ..authenticator import Authenticator
from ..bus import Bus
from ..identity import Identity
from ..inbound_bus import LocalBus
from ..message import InboundMessage, OutboundMessage, Platform, Response
from ..pool import Pool
from ..render_events import TextRenderEvent
from ..tts_dispatch import AudioPipeline
from .hub_protocol import (  # noqa: F401 — public re-export
    Binding,
    ChannelAdapter,
    RoutingKey,
)
from .hub_rate_limit import RateLimiter
from .identity_resolver import IdentityResolver
from .message_pipeline import (  # noqa: F401 — public re-export
    Action,
    PipelineResult,
)
from .middleware import build_default_pipeline
from .pool_manager import PoolManager

if TYPE_CHECKING:
    from collections import deque
    from collections.abc import AsyncIterator

    from ...stt import STTProtocol
    from ...tts import TtsProtocol
    from ..circuit_breaker import CircuitRegistry
    from ..cli_pool import CliPool
    from ..memory import MemoryManager
    from ..message import OutboundAttachment, OutboundAudio, OutboundAudioChunk
    from ..messages import MessageManager
    from ..render_events import RenderEvent
    from ..stores.identity_alias_store import IdentityAliasStore
    from ..stores.message_index import MessageIndex
    from ..stores.pairing import PairingManager
    from ..stores.prefs_store import PrefsStore
    from ..stores.turn_store import TurnStore
    from .event_bus import PipelineEventBus
    from .outbound_dispatcher import OutboundDispatcher

log = logging.getLogger(__name__)


class Hub:
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
        stt: "STTProtocol | None" = None,
        tts: "TtsProtocol | None" = None,
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
        inbound_bus: "Bus[InboundMessage] | None" = None,
    ) -> None:
        self._platform_queue_maxsize = platform_queue_maxsize
        self.inbound_bus: Bus[InboundMessage] = inbound_bus or LocalBus(
            name="inbound",
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
        self._stt: STTProtocol | None = stt
        self._tts: TtsProtocol | None = tts
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
        self._alias_store: IdentityAliasStore | None = None
        # Identity resolver: pure logic, no I/O
        self._identity_resolver = IdentityResolver(
            authenticators=self._authenticators,
            bindings=self.bindings,
        )

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

    def set_alias_store(self, store: IdentityAliasStore) -> None:
        self._alias_store = store
        # Inject into memory manager if present
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

        Delegates to IdentityResolver.
        """
        return self._identity_resolver.resolve_identity(user_id, platform, bot_id)

    def _resolve_message_trust(self, msg: InboundMessage) -> InboundMessage:
        """Re-resolve trust level on the Hub side (C3 — trust re-resolution).

        Delegates to IdentityResolver.
        """
        return self._identity_resolver.resolve_message_trust(msg)

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
        """Resolve binding: exact key, then wildcard fallback, else None.

        Delegates to IdentityResolver. See IdentityResolver.resolve_binding for details.
        """
        return self._identity_resolver.resolve_binding(msg)

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

    # -----------------------------------------------------------------------
    # Outbound routing methods (consolidated from hub_outbound.py per ADR-025 F-3)
    # -----------------------------------------------------------------------

    async def _route_outbound(
        self,
        msg: InboundMessage,
        enqueue_fn: Callable[[OutboundDispatcher], None],
        fallback_fn: Callable[[ChannelAdapter], Coroutine[Any, Any, None]],
        *,
        resource: str = "response",
    ) -> None:
        """Core outbound routing: dispatcher queue -> direct adapter fallback."""
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

    async def dispatch_response(
        self,
        msg: InboundMessage,
        response: Response | OutboundMessage,
    ) -> None:
        """Send response back via the originating adapter."""
        if isinstance(response, OutboundMessage):
            outbound = response
        else:
            outbound = response.to_outbound()
            _cb = response.metadata.get("_on_dispatched")
            if _cb is not None:
                outbound.metadata["_on_dispatched"] = _cb
        if outbound.routing is None and msg.routing is not None:
            outbound.routing = msg.routing

        async def _fallback_and_notify(adapter: ChannelAdapter) -> None:
            await adapter.send(msg, outbound)
            _dispatched = outbound.metadata.pop("_on_dispatched", None)
            if callable(_dispatched):
                _result = _dispatched(outbound)
                if inspect.isawaitable(_result):
                    await _result

        await self._route_outbound(
            msg,
            enqueue_fn=lambda d: d.enqueue(msg, outbound),
            fallback_fn=_fallback_and_notify,
            resource="responses",
        )

        if isinstance(response, Response) and response.audio:
            await self.dispatch_audio(msg, response.audio)

        _should_speak = msg.modality == "voice" or (
            isinstance(response, Response) and response.speak
        )
        if _should_speak and self._tts is not None:
            text = outbound.to_text().strip()
            if text:
                agent_tts = self._audio_pipeline.resolve_agent_tts(msg)
                fallback_lang = (
                    self._audio_pipeline._resolve_agent_fallback_language(msg)
                )
                task = asyncio.create_task(
                    self._audio_pipeline.synthesize_and_dispatch_audio(
                        msg,
                        text,
                        agent_tts=agent_tts,
                        fallback_language=fallback_lang,
                        **self._audio_pipeline.tts_language_kwargs(msg),
                    ),
                    name=f"tts:{msg.id}",
                )
                self._memory_tasks.add(task)
                task.add_done_callback(self._memory_tasks.discard)

    async def dispatch_streaming(  # noqa: C901, PLR0915
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[RenderEvent],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response back via the originating adapter.

        For voice modality: text is streamed to the user immediately (so they
        see it appearing) while also being collected for TTS synthesis.  After
        the stream completes, TTS runs as a background task and the resulting
        audio is dispatched as a voice note.
        """
        _should_speak = msg.modality == "voice" and self._tts is not None
        _voice_parts: list[str] | None = None
        _voice_done: asyncio.Event | None = None

        if _should_speak:
            # Tee the stream: forward chunks through the normal streaming path
            # while collecting text for TTS synthesis after streaming completes.
            _voice_parts = []
            _voice_done = asyncio.Event()
            _raw = chunks

            async def _tee() -> AsyncIterator[RenderEvent]:
                try:
                    async for event in _raw:
                        if isinstance(event, TextRenderEvent):
                            _voice_parts.append(event.text)
                        # ToolSummaryRenderEvent: skip — voice only needs text
                        yield event
                finally:
                    _voice_done.set()

            chunks = _tee()

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
            # Voice TTS: don't block here — fire TTS as a background task
            # that waits for the tee to finish independently.  Blocking on
            # _voice_done.wait() caused the pool processor to hang when the
            # dispatcher's scope lock was held by a prior stream (#TTS-fix).
            if _should_speak and _voice_done is not None:
                _vp = _voice_parts
                _vd = _voice_done
                assert _vd is not None

                async def _deferred_tts() -> None:
                    await _vd.wait()
                    full_text = "".join(_vp or []).strip()
                    if full_text:
                        agent_tts = self._audio_pipeline.resolve_agent_tts(msg)
                        fallback_lang = (
                            self._audio_pipeline._resolve_agent_fallback_language(msg)
                        )
                        await self._audio_pipeline.synthesize_and_dispatch_audio(
                            msg,
                            full_text,
                            agent_tts=agent_tts,
                            fallback_language=fallback_lang,
                            **self._audio_pipeline.tts_language_kwargs(msg),
                        )

                task = asyncio.create_task(_deferred_tts(), name=f"tts:{msg.id}")
                self._memory_tasks.add(task)
                task.add_done_callback(self._memory_tasks.discard)
                return
        else:
            adapter = self.adapter_registry.get((platform, msg.bot_id))
            if adapter is None:
                raise KeyError(
                    f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                    "Call register_adapter() before dispatching responses."
                )
            if hasattr(adapter, "send_streaming"):
                await adapter.send_streaming(msg, chunks, outbound)
            else:
                if outbound is not None:
                    log.warning(
                        "Adapter for %s lacks send_streaming; "
                        "reply_message_id will not be recorded",
                        msg.platform,
                    )
                text = ""
                async for event in chunks:
                    if isinstance(event, TextRenderEvent):
                        text += event.text
                if text:
                    await adapter.send(msg, OutboundMessage.from_text(text))
                else:
                    log.debug(
                        "dispatch_streaming fallback: no text events in stream"
                        " — skipping send for msg %s",
                        msg.id,
                    )
            if outbound is not None:
                _dispatched = outbound.metadata.pop("_on_dispatched", None)
                if callable(_dispatched):
                    _result = _dispatched(outbound)
                    if inspect.isawaitable(_result):
                        await _result
            self._last_processed_at = time.monotonic()

        # Voice: synthesize TTS as a background task now that text is collected.
        if _should_speak:
            full_text = "".join(_voice_parts or []).strip()
            if full_text:
                agent_tts = self._audio_pipeline.resolve_agent_tts(msg)
                fallback_lang = (
                    self._audio_pipeline._resolve_agent_fallback_language(msg)
                )
                task = asyncio.create_task(
                    self._audio_pipeline.synthesize_and_dispatch_audio(
                        msg,
                        full_text,
                        agent_tts=agent_tts,
                        fallback_language=fallback_lang,
                        **self._audio_pipeline.tts_language_kwargs(msg),
                    ),
                    name=f"tts:{msg.id}",
                )
                self._memory_tasks.add(task)
                task.add_done_callback(self._memory_tasks.discard)

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
            result.pool.submit(result.msg if result.msg is not None else msg)

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
            if pool.is_idle:
                # Subprocess alive but not processing — no in-flight turn to warn about.
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
