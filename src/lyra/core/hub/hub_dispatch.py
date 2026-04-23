"""Outbound dispatch mixin for Hub — split from hub.py (#760).

Provides _route_outbound(), dispatch_response(), dispatch_streaming(),
dispatch_attachment(), dispatch_audio(), dispatch_audio_stream(),
dispatch_voice_stream(), and _dispatch_pipeline_result() for Hub.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ..messaging.message import (
        InboundMessage,
        OutboundAttachment,
        OutboundAudio,
        OutboundAudioChunk,
        OutboundMessage,
        Response,
    )
    from ..messaging.render_events import RenderEvent
    from .hub_protocol import ChannelAdapter
    from .outbound import OutboundDispatcher, OutboundRouter
    from .pipeline.message_pipeline import PipelineResult

log = logging.getLogger(__name__)


class HubDispatchMixin:
    """Mixin providing outbound dispatch for Hub."""

    # Declared for type-checking — initialised by Hub.__init__.
    if TYPE_CHECKING:
        _outbound_router: OutboundRouter

    # -----------------------------------------------------------------------
    # Outbound dispatch delegation (issue #753 Phase 3)
    # -----------------------------------------------------------------------

    async def _route_outbound(
        self,
        msg: InboundMessage,
        enqueue_fn: Callable[[OutboundDispatcher], None],
        fallback_fn: Callable[[ChannelAdapter], Coroutine[Any, Any, None]],
        *,
        resource: str = "response",
    ) -> None:
        """Core outbound routing. Delegates to OutboundRouter."""
        await self._outbound_router._route_outbound(
            msg, enqueue_fn, fallback_fn, resource=resource
        )

    async def dispatch_response(
        self,
        msg: InboundMessage,
        response: Response | OutboundMessage,
    ) -> None:
        """Send response via originating adapter. Delegates to OutboundRouter."""
        await self._outbound_router.dispatch_response(msg, response)

    async def dispatch_streaming(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[RenderEvent],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response via originating adapter. Delegates to OutboundRouter."""
        await self._outbound_router.dispatch_streaming(msg, chunks, outbound)

    async def dispatch_attachment(
        self,
        msg: InboundMessage,
        attachment: OutboundAttachment,
    ) -> None:
        """Send attachment via originating adapter. Delegates to OutboundRouter."""
        await self._outbound_router.dispatch_attachment(msg, attachment)

    async def dispatch_audio(
        self,
        msg: InboundMessage,
        audio: OutboundAudio,
    ) -> None:
        """Send audio via originating adapter. Delegates to OutboundRouter."""
        await self._outbound_router.dispatch_audio(msg, audio)

    async def dispatch_audio_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[OutboundAudioChunk],
    ) -> None:
        """Stream audio via originating adapter. Delegates to OutboundRouter."""
        await self._outbound_router.dispatch_audio_stream(msg, chunks)

    async def dispatch_voice_stream(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[OutboundAudioChunk],
    ) -> None:
        """Stream voice via Discord session. Delegates to OutboundRouter."""
        await self._outbound_router.dispatch_voice_stream(msg, chunks)

    # -----------------------------------------------------------------------
    # Pipeline result dispatch
    # -----------------------------------------------------------------------

    async def _dispatch_pipeline_result(
        self,
        msg: InboundMessage,
        result: PipelineResult,
    ) -> None:
        """Dispatch a pipeline result: send command response or submit to pool."""
        from .pipeline.pipeline_types import Action

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
