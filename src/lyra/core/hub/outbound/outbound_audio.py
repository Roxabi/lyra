"""Audio and attachment dispatch helper — extracted from outbound_router.py.

This module owns simple media dispatch methods that each delegate to
OutboundRouter._route_outbound without additional logic:

- dispatch_attachment: send an OutboundAttachment
- dispatch_audio: single OutboundAudio voice note
- dispatch_audio_stream: streaming OutboundAudioChunk sequence
- dispatch_voice_stream: TTS audio chunks to an active Discord voice session

TTS synthesis/dispatch is handled by TtsDispatch (outbound_tts.py), not here.
Extracted per issue #760 for line count management.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import TYPE_CHECKING, Any

from ...messaging.message import InboundMessage

if TYPE_CHECKING:
    from ...messaging.message import (
        OutboundAttachment,
        OutboundAudio,
        OutboundAudioChunk,
    )

# Type alias for OutboundRouter._route_outbound (variadic to accommodate
# `resource=` kwarg; see outbound_router.py for concrete signature)
_RouteOutbound = Callable[..., Coroutine[Any, Any, None]]


class AudioDispatch:
    """Audio and attachment dispatch helper.

    Owns simple media dispatch methods extracted from OutboundRouter:
    dispatch_attachment, dispatch_audio, dispatch_audio_stream, and
    dispatch_voice_stream. Each method delegates routing back to
    OutboundRouter._route_outbound via an injected callable, keeping
    this module free of routing and platform concerns.
    """

    def __init__(self, route_outbound: "_RouteOutbound") -> None:
        self._route_outbound = route_outbound

    async def dispatch_attachment(
        self,
        msg: InboundMessage,
        attachment: "OutboundAttachment",
    ) -> None:
        """Send an attachment back via the originating adapter."""
        await self._route_outbound(
            msg,
            lambda d: d.enqueue_attachment(msg, attachment),
            lambda a: a.render_attachment(attachment, msg),
        )

    async def dispatch_audio(
        self,
        msg: InboundMessage,
        audio: "OutboundAudio",
    ) -> None:
        """Send an audio voice note back via the originating adapter."""
        await self._route_outbound(
            msg,
            lambda d: d.enqueue_audio(msg, audio),
            lambda a: a.render_audio(audio, msg),
        )

    async def dispatch_audio_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator["OutboundAudioChunk"],
    ) -> None:
        """Stream audio chunks back via the originating adapter."""
        await self._route_outbound(
            msg,
            lambda d: d.enqueue_audio_stream(msg, chunks),
            lambda a: a.render_audio_stream(chunks, msg),
        )

    async def dispatch_voice_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator["OutboundAudioChunk"],
    ) -> None:
        """Stream TTS audio to an active Discord voice session."""
        await self._route_outbound(
            msg,
            lambda d: d.enqueue_voice_stream(msg, chunks),
            lambda a: a.render_voice_stream(chunks, msg),
        )
