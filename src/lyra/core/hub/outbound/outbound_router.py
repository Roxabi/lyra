"""OutboundRouter — unified outbound routing and dispatch coordinator.

Consolidates all dispatch_* methods extracted from Hub (issue #753 Phase 3).
Routes between dispatcher queue and direct adapter fallback.

Hub → OutboundRouter delegates dispatch_* calls. TTS logic lives in
TtsDispatch (outbound_tts.py); audio/attachment logic in AudioDispatch
(outbound_audio.py); streaming-dispatch logic in StreamingDispatch
(outbound_streaming.py). All extracted per issue #760.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import TYPE_CHECKING, Any

from ...messaging.callbacks import unwrap_callback
from ...messaging.message import InboundMessage, OutboundMessage, Platform, Response
from .outbound_audio import AudioDispatch
from .outbound_streaming import StreamingDispatch
from .outbound_tts import TtsDispatch

if TYPE_CHECKING:
    from ...circuit_breaker import CircuitRegistry
    from ...messaging.message import (
        OutboundAttachment,
        OutboundAudio,
        OutboundAudioChunk,
    )
    from ...messaging.messages import MessageManager
    from ...messaging.render_events import RenderEvent
    from ...tts_dispatch import AudioPipeline
    from ..hub_protocol import ChannelAdapter
    from .outbound_dispatcher import OutboundDispatcher

# Re-export for API preservation
__all__ = ["AudioDispatch", "OutboundRouter", "TtsDispatch"]

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
        # TTS dispatch helper (extracted per #760)
        self._tts_dispatch = TtsDispatch(
            audio_pipeline=audio_pipeline,
            tts=tts,
            memory_tasks=memory_tasks,
        )
        # Audio dispatch helper (extracted per #760)
        self._audio_dispatch = AudioDispatch(route_outbound=self._route_outbound)
        # Streaming dispatch helper (extracted per #760)
        self._streaming_dispatch = StreamingDispatch(
            adapters=adapters,
            dispatchers=dispatchers,
            tts_dispatch=self._tts_dispatch,
            get_tts=lambda: self._tts,
            get_audio_pipeline=lambda: self._audio_pipeline,
        )

    def set_msg_manager(self, msg_manager: "MessageManager | None") -> None:
        """Update message manager reference (called after Hub construction)."""
        self._msg_manager = msg_manager

    def set_tts(self, tts: "object | None") -> None:
        """Update TTS reference (called after Hub construction)."""
        self._tts = tts
        self._tts_dispatch.set_tts(tts)

    def set_memory_tasks(self, tasks: "set[asyncio.Task] | None") -> None:
        """Update memory tasks set reference (called after Hub construction)."""
        self._memory_tasks = tasks
        self._tts_dispatch.set_memory_tasks(tasks)

    def set_audio_pipeline(self, pipeline: "AudioPipeline | None") -> None:
        """Update audio pipeline reference (called after Hub construction)."""
        self._audio_pipeline = pipeline
        self._tts_dispatch.set_audio_pipeline(pipeline)

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
            _cb = unwrap_callback(response.metadata, "_on_dispatched", pop=True)
            if _cb is not None:
                outbound.metadata["_on_dispatched"] = _cb
        if outbound.routing is None and msg.routing is not None:
            outbound.routing = msg.routing

        async def _fallback_and_notify(adapter: "ChannelAdapter") -> None:
            await adapter.send(msg, outbound)
            _dispatched = unwrap_callback(outbound.metadata, "_on_dispatched", pop=True)
            if _dispatched is not None:
                await _dispatched(outbound)

        await self._route_outbound(
            msg,
            enqueue_fn=lambda d: d.enqueue(msg, outbound),
            fallback_fn=_fallback_and_notify,
            resource="responses",
        )

        if isinstance(response, Response) and response.audio:
            await self.dispatch_audio(msg, response.audio)

        if self._tts_dispatch.should_speak(msg, response):
            await self._tts_dispatch.dispatch_tts_for_response(msg, outbound)

    async def dispatch_streaming(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator["RenderEvent"],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response via the streaming-dispatch helper."""
        self._last_processed_at = await self._streaming_dispatch.dispatch(
            msg, chunks, outbound
        )

    async def dispatch_attachment(
        self,
        msg: InboundMessage,
        attachment: "OutboundAttachment",
    ) -> None:
        """Send an attachment back via the originating adapter."""
        await self._audio_dispatch.dispatch_attachment(msg, attachment)

    async def dispatch_audio(
        self,
        msg: InboundMessage,
        audio: "OutboundAudio",
    ) -> None:
        """Send an audio voice note back via the originating adapter."""
        await self._audio_dispatch.dispatch_audio(msg, audio)

    async def dispatch_audio_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator["OutboundAudioChunk"],
    ) -> None:
        """Stream audio chunks back via the originating adapter."""
        await self._audio_dispatch.dispatch_audio_stream(msg, chunks)

    async def dispatch_voice_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator["OutboundAudioChunk"],
    ) -> None:
        """Stream TTS audio to an active Discord voice session."""
        await self._audio_dispatch.dispatch_voice_stream(msg, chunks)
