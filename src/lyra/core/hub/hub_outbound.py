"""Outbound routing mixin for Hub.

Extracted from hub.py to keep it ≤300 lines (epic #294).
Contains all dispatch_* methods and outbound routing logic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from lyra.errors import ProviderError

from ..message import OutboundMessage, Platform, Response
from ..render_events import TextRenderEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ...tts import TTSService
    from ..agent import AgentBase
    from ..agent_config import AgentTTSConfig
    from ..audio_pipeline import AudioPipeline
    from ..circuit_breaker import CircuitRegistry
    from ..message import (
        InboundMessage,
        OutboundAttachment,
        OutboundAudio,
        OutboundAudioChunk,
    )
    from ..messages import MessageManager
    from ..render_events import RenderEvent
    from .hub_protocol import ChannelAdapter
    from .outbound_dispatcher import OutboundDispatcher
    from .pool_manager import PoolManager

log = logging.getLogger(__name__)


class HubOutboundMixin:
    """Outbound routing methods extracted from Hub."""

    # These attributes are defined on Hub — declared here for type checking only.
    if TYPE_CHECKING:
        outbound_dispatchers: dict[tuple[Platform, str], OutboundDispatcher]
        adapter_registry: dict[tuple[Platform, str], ChannelAdapter]
        circuit_registry: CircuitRegistry | None
        _msg_manager: MessageManager | None
        _tts: TTSService | None
        _audio_pipeline: AudioPipeline
        _memory_tasks: set[asyncio.Task]
        _last_processed_at: float | None
        agent_registry: dict[str, AgentBase]
        _pool_manager: PoolManager

        def resolve_binding(
            self, msg: InboundMessage
        ) -> Any: ...  # returns Binding | None

    def _resolve_agent_tts(self, msg: "InboundMessage") -> "AgentTTSConfig | None":
        """Resolve per-agent TTS config from the message's binding."""
        binding = self.resolve_binding(msg)
        if binding is None:
            return None
        agent = self.agent_registry.get(binding.agent_name)
        if agent is None:
            log.warning(
                "Agent %r from binding not in registry — using global TTS defaults",
                binding.agent_name,
            )
            return None
        return agent.config.voice.tts if agent.config.voice is not None else None

    def _resolve_pool(self, msg: "InboundMessage") -> Any:
        """Resolve pool from message routing (for session-level state)."""
        from .hub_protocol import RoutingKey

        key = RoutingKey(Platform(msg.platform), msg.bot_id, msg.scope_id)
        pool_id = key.to_pool_id()
        return self._pool_manager.pools.get(pool_id) if hasattr(self, "_pool_manager") else None

    def _tts_language_kwargs(self, msg: "InboundMessage") -> dict:
        """Build session_language + on_language_detected kwargs for TTS."""
        pool = self._resolve_pool(msg)
        if pool is None:
            return {}
        return {
            "session_language": pool.last_detected_language,
            "on_language_detected": lambda lang: setattr(
                pool,
                "last_detected_language",
                lang,
            ),
        }

    def _resolve_agent_fallback_language(self, msg: "InboundMessage") -> str | None:
        """Resolve per-agent fallback_language from the message's binding (#343)."""
        binding = self.resolve_binding(msg)
        if binding is None:
            return None
        agent = self.agent_registry.get(binding.agent_name)
        if agent is None:
            return None
        return agent.config.i18n_language

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
                _dispatched(outbound)

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
                agent_tts = self._resolve_agent_tts(msg)
                fallback_lang = self._resolve_agent_fallback_language(msg)
                task = asyncio.create_task(
                    self._audio_pipeline.synthesize_and_dispatch_audio(
                        msg,
                        text,
                        agent_tts=agent_tts,
                        fallback_language=fallback_lang,
                        **self._tts_language_kwargs(msg),
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
                        agent_tts = self._resolve_agent_tts(msg)
                        fallback_lang = self._resolve_agent_fallback_language(msg)
                        await self._audio_pipeline.synthesize_and_dispatch_audio(
                            msg,
                            full_text,
                            agent_tts=agent_tts,
                            fallback_language=fallback_lang,
                            **self._tts_language_kwargs(msg),
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
                    _dispatched(outbound)
            self._last_processed_at = time.monotonic()

        # Voice: synthesize TTS as a background task now that text is collected.
        if _should_speak:
            full_text = "".join(_voice_parts or []).strip()
            if full_text:
                agent_tts = self._resolve_agent_tts(msg)
                fallback_lang = self._resolve_agent_fallback_language(msg)
                task = asyncio.create_task(
                    self._audio_pipeline.synthesize_and_dispatch_audio(
                        msg,
                        full_text,
                        agent_tts=agent_tts,
                        fallback_language=fallback_lang,
                        **self._tts_language_kwargs(msg),
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
