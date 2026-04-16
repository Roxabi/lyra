"""OutboundRouter — unified outbound routing and dispatch coordinator.

Consolidates all dispatch_* methods extracted from Hub (issue #753 Phase 3).
This class owns the routing logic that selects between dispatcher queue and
direct adapter fallback, plus TTS integration for voice responses.

Architecture:
- Hub owns adapter_registry, outbound_dispatchers, audio_pipeline, circuit_registry
- OutboundRouter receives references to these at construction
- Hub delegates dispatch_* calls to the router
- PoolContext protocol is satisfied via delegation chain: Pool -> Hub -> Router

This separation keeps Hub focused on coordination while routing logic
lives in a dedicated component (~300 lines extracted).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import TYPE_CHECKING, Any

from ..message import InboundMessage, OutboundMessage, Platform, Response
from ..render_events import TextRenderEvent

if TYPE_CHECKING:
    from ..circuit_breaker import CircuitRegistry
    from ..message import OutboundAttachment, OutboundAudio, OutboundAudioChunk
    from ..messages import MessageManager
    from ..render_events import RenderEvent
    from ..tts_dispatch import AudioPipeline
    from .hub_protocol import ChannelAdapter
    from .outbound_dispatcher import OutboundDispatcher

log = logging.getLogger(__name__)


class OutboundRouter:
    """Unified outbound routing and dispatch coordinator.

    Handles all outbound message routing: response, streaming, attachments,
    audio, and voice streams. Routes through OutboundDispatcher queue
    when available, falls back to direct adapter calls otherwise.

    Owns TTS integration for voice responses.
    """

    def __init__(  # noqa: PLR0913
        self,
        adapters: dict[tuple[Platform, str], "ChannelAdapter"],
        dispatchers: dict[tuple[Platform, str], "OutboundDispatcher"],
        audio_pipeline: "AudioPipeline | None" = None,
        circuit_registry: "CircuitRegistry | None" = None,
        msg_manager: "MessageManager | None" = None,
        tts: "object | None" = None,
        memory_tasks: "set[asyncio.Task] | None" = None,
    ) -> None:
        self._adapters = adapters
        self._dispatchers = dispatchers
        self._audio_pipeline = audio_pipeline
        self._circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._tts = tts
        self._memory_tasks = memory_tasks
        self._last_processed_at: float | None = None

    def set_msg_manager(self, msg_manager: "MessageManager | None") -> None:
        """Update message manager reference (called after Hub construction)."""
        self._msg_manager = msg_manager

    def set_tts(self, tts: "object | None") -> None:
        """Update TTS reference (called after Hub construction)."""
        self._tts = tts

    def set_memory_tasks(self, tasks: "set[asyncio.Task] | None") -> None:
        """Update memory tasks set reference (called after Hub construction)."""
        self._memory_tasks = tasks

    def set_audio_pipeline(self, pipeline: "AudioPipeline | None") -> None:
        """Update audio pipeline reference (called after Hub construction)."""
        self._audio_pipeline = pipeline

    @property
    def last_processed_at(self) -> float | None:
        """Return timestamp of last processed outbound (for health checks)."""
        return self._last_processed_at

    # -----------------------------------------------------------------------
    # Core routing logic
    # -----------------------------------------------------------------------

    async def _route_outbound(
        self,
        msg: InboundMessage,
        enqueue_fn: Callable[["OutboundDispatcher"], None],
        fallback_fn: Callable[["ChannelAdapter"], Coroutine[Any, Any, None]],
        *,
        resource: str = "response",
    ) -> None:
        """Core outbound routing: dispatcher queue -> direct adapter fallback."""
        try:
            platform = Platform(msg.platform)
        except ValueError:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                f"Call register_adapter() before dispatching {resource}."
            ) from None
        dispatcher = self._dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            enqueue_fn(dispatcher)
            self._last_processed_at = time.monotonic()
            return
        adapter = self._adapters.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                f"Call register_adapter() before dispatching {resource}."
            )
        await fallback_fn(adapter)
        self._last_processed_at = time.monotonic()

    # -----------------------------------------------------------------------
    # Dispatch methods
    # -----------------------------------------------------------------------

    async def dispatch_response(  # noqa: C901
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

        async def _fallback_and_notify(adapter: "ChannelAdapter") -> None:
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
        if _should_speak and self._tts is not None and self._audio_pipeline is not None:
            text = outbound.to_text().strip()
            if text:
                agent_tts = (
                    self._audio_pipeline.resolve_agent_tts(msg)
                    if self._audio_pipeline
                    else None
                )
                fallback_lang = (
                    self._audio_pipeline._resolve_agent_fallback_language(msg)
                    if self._audio_pipeline
                    else None
                )
                task = asyncio.create_task(
                    self._audio_pipeline.synthesize_and_dispatch_audio(
                        msg,
                        text,
                        agent_tts=agent_tts,
                        fallback_language=fallback_lang,
                        **(
                            self._audio_pipeline.tts_language_kwargs(msg)
                            if self._audio_pipeline
                            else {}
                        ),
                    ),
                    name=f"tts:{msg.id}",
                )
                if self._memory_tasks is not None:
                    self._memory_tasks.add(task)
                    task.add_done_callback(self._memory_tasks.discard)

    async def dispatch_streaming(  # noqa: C901, PLR0915
        self,
        msg: InboundMessage,
        chunks: AsyncIterator["RenderEvent"],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response back via the originating adapter.

        For voice modality: text is streamed to the user immediately (so they
        see it appearing) while also being collected for TTS synthesis.  After
        the stream completes, TTS runs as a background task and the resulting
        audio is dispatched as a voice note.
        """
        _should_speak = (
            msg.modality == "voice"
            and self._tts is not None
            and self._audio_pipeline is not None
        )
        _voice_parts: list[str] | None = None
        _voice_done: asyncio.Event | None = None

        if _should_speak:
            # Tee the stream: forward chunks through the normal streaming path
            # while collecting text for TTS synthesis after streaming completes.
            _voice_parts = []
            _voice_done = asyncio.Event()
            _raw = chunks

            async def _tee() -> AsyncIterator["RenderEvent"]:
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

        try:
            platform = Platform(msg.platform)
        except ValueError:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching streaming responses."
            ) from None
        dispatcher = self._dispatchers.get((platform, msg.bot_id))
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
                        # _should_speak guarantees _audio_pipeline is not None
                        assert self._audio_pipeline is not None
                        audio_pipeline = self._audio_pipeline  # for type narrowing
                        agent_tts = audio_pipeline.resolve_agent_tts(msg)
                        fallback_lang = audio_pipeline._resolve_agent_fallback_language(
                            msg
                        )
                        await audio_pipeline.synthesize_and_dispatch_audio(
                            msg,
                            full_text,
                            agent_tts=agent_tts,
                            fallback_language=fallback_lang,
                            **audio_pipeline.tts_language_kwargs(msg),
                        )

                task = asyncio.create_task(_deferred_tts(), name=f"tts:{msg.id}")
                if self._memory_tasks is not None:
                    self._memory_tasks.add(task)
                    task.add_done_callback(self._memory_tasks.discard)
                return
        else:
            adapter = self._adapters.get((platform, msg.bot_id))
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
                # _should_speak guarantees _audio_pipeline is not None
                assert self._audio_pipeline is not None
                audio_pipeline = self._audio_pipeline  # for type narrowing
                agent_tts = audio_pipeline.resolve_agent_tts(msg)
                fallback_lang = audio_pipeline._resolve_agent_fallback_language(msg)
                task = asyncio.create_task(
                    audio_pipeline.synthesize_and_dispatch_audio(
                        msg,
                        full_text,
                        agent_tts=agent_tts,
                        fallback_language=fallback_lang,
                        **audio_pipeline.tts_language_kwargs(msg),
                    ),
                    name=f"tts:{msg.id}",
                )
                if self._memory_tasks is not None:
                    self._memory_tasks.add(task)
                    task.add_done_callback(self._memory_tasks.discard)

    async def dispatch_attachment(
        self,
        msg: InboundMessage,
        attachment: "OutboundAttachment",
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
        audio: "OutboundAudio",
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
        chunks: AsyncIterator["OutboundAudioChunk"],
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
        chunks: AsyncIterator["OutboundAudioChunk"],
    ) -> None:
        """Stream TTS audio to an active Discord voice session."""
        await self._route_outbound(
            msg,
            enqueue_fn=lambda d: d.enqueue_voice_stream(msg, chunks),
            fallback_fn=lambda a: a.render_voice_stream(chunks, msg),
            resource="voice stream",
        )
