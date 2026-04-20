"""StreamingDispatch — streaming-response routing extracted from OutboundRouter (#760).

Handles the voice-tee setup, dispatcher-queue vs direct-adapter routing, and
the fallback text-accumulation path used when an adapter lacks ``send_streaming``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from ..message import InboundMessage, OutboundMessage, Platform
from ..render_events import TextRenderEvent

if TYPE_CHECKING:
    from ..render_events import RenderEvent
    from ..tts_dispatch import AudioPipeline
    from .hub_protocol import ChannelAdapter
    from .outbound_dispatcher import OutboundDispatcher
    from .outbound_tts import TtsDispatch

log = logging.getLogger(__name__)


class StreamingDispatch:
    """Routes streaming responses with optional voice TTS tee."""

    def __init__(
        self,
        *,
        adapters: dict[tuple[Platform, str], "ChannelAdapter"],
        dispatchers: dict[tuple[Platform, str], "OutboundDispatcher"],
        tts_dispatch: "TtsDispatch",
        get_tts: Callable[[], object | None],
        get_audio_pipeline: Callable[[], "AudioPipeline | None"],
    ) -> None:
        self._adapters = adapters
        self._dispatchers = dispatchers
        self._tts_dispatch = tts_dispatch
        self._get_tts = get_tts
        self._get_audio_pipeline = get_audio_pipeline

    async def dispatch(  # noqa: C901, PLR0915
        self,
        msg: InboundMessage,
        chunks: AsyncIterator["RenderEvent"],
        outbound: OutboundMessage | None = None,
    ) -> float:
        """Stream response back via the originating adapter.

        Returns the ``time.monotonic()`` timestamp of dispatch completion so
        the caller can update its ``last_processed_at`` marker.
        """
        _should_speak = (
            msg.modality == "voice"
            and self._get_tts() is not None
            and self._get_audio_pipeline() is not None
        )
        _voice_parts: list[str] | None = None
        _voice_done: asyncio.Event | None = None

        if _should_speak:
            chunks, _voice_parts, _voice_done = self._tts_dispatch.create_streaming_tee(
                chunks
            )

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
            stamp = time.monotonic()
            # Don't block — deferred TTS waits for tee finish independently.
            # Blocking on _voice_done.wait() hung pool processor when dispatcher
            # scope lock was held by a prior stream (#TTS-fix).
            if _should_speak and _voice_done is not None and _voice_parts is not None:
                self._tts_dispatch.create_deferred_tts_task(
                    msg, _voice_parts, _voice_done
                )
            return stamp

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
        stamp = time.monotonic()

        # Voice: synthesize TTS after text collected (fallback path)
        if _should_speak and _voice_parts is not None:
            await self._tts_dispatch.dispatch_tts_from_parts(msg, _voice_parts)

        return stamp
