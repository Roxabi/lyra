"""Streaming helpers extracted from pool_processor_exec (issue #753).

Contains the async generator capture wrapper and streaming turn-logging callback.
"""

from __future__ import annotations

import collections.abc
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..messaging.message import InboundMessage
    from .pool import Pool

from ..messaging.message import OutboundMessage, Response
from ..messaging.render_events import RenderEvent, TextDeltaRenderEvent
from .pool_observer import TurnLogDeps

log = logging.getLogger(__name__)


def build_streaming_capture(
    result_iter: collections.abc.AsyncIterator[RenderEvent],
    content_parts: list[str],
    pool: Pool,
    stream_done_event: object | None,
) -> collections.abc.AsyncGenerator[RenderEvent, None]:
    """Build an async generator that captures TextDeltaRenderEvent content.

    Wraps the result iterator to collect text content for turn logging while
    forwarding all events. v1 TextRenderEvent / ToolSummaryRenderEvent removed
    in Slice 5 (#1192); content is now accumulated from TextDeltaRenderEvent deltas.
    """

    async def _capture() -> collections.abc.AsyncGenerator[RenderEvent, None]:
        try:
            async for event in result_iter:
                if isinstance(event, TextDeltaRenderEvent):
                    content_parts.append(event.delta)
                yield event
        finally:
            _aclose = getattr(result_iter, "aclose", None)
            if callable(_aclose):
                await _aclose()  # type: ignore[misc] — DEBT:defensive-narrow-payloads
            if stream_done_event is not None:
                stream_done_event.set()  # type: ignore[misc] — DEBT:defensive-narrow-payloads

    return _capture()


@dataclass(frozen=True)
class StreamLogDeps:
    """Dependencies for build_streaming_turn_logger."""

    pool: Pool
    result_iter_for_sid: collections.abc.AsyncIterator[RenderEvent]
    original_msg: InboundMessage
    platform: str
    user_id: str
    content_parts: list[str]


def build_streaming_turn_logger(
    deps: StreamLogDeps,
) -> tuple[OutboundMessage, Callable[[OutboundMessage], Awaitable[None]]]:
    """Build OutboundMessage with turn-logging callback for streaming responses.

    Returns:
        Tuple of (outbound_msg, log_callback) where callback should be attached
        to outbound.metadata["_on_dispatched"].
    """
    outbound = OutboundMessage.from_text("")

    async def _log_streaming_turn(outbound_msg: OutboundMessage) -> None:
        # Clear inflight reference once streaming is fully delivered.
        if deps.pool._inflight_stream_outbound is outbound_msg:
            deps.pool._inflight_stream_outbound = None
        # Propagate CLI session_id from the (now-consumed) iterator.
        _stream_sid = getattr(deps.result_iter_for_sid, "session_id", None)
        if _stream_sid and deps.pool.session_id != _stream_sid:
            await deps.pool._observer.end_session_async(deps.pool.session_id)
            deps.pool.session_id = _stream_sid
        await deps.pool._observer.session_update_async(deps.original_msg)
        _reply_id = outbound_msg.metadata.get("reply_message_id")
        await deps.pool._observer.log_turn_async(
            TurnLogDeps(
                role="assistant",
                platform=deps.platform,
                user_id=deps.user_id,
                content="".join(deps.content_parts),
                reply_message_id=(str(_reply_id) if _reply_id is not None else None),
            )
        )
        # Index assistant turn for reply-to session routing (#341).
        await deps.pool._observer.index_turn_async(
            str(_reply_id) if _reply_id is not None else None,
            session_id=deps.pool.session_id,
            role="assistant",
        )

    return outbound, _log_streaming_turn


async def run_streaming_turn_post(
    processor: object | None,
    stream_done_event: object | None,
    original_msg: InboundMessage,
    content_parts: list[str],
) -> None:
    """Run processor post-hook after streaming is fully consumed (#372)."""
    if processor is None or stream_done_event is None:
        return
    await stream_done_event.wait()  # type: ignore[misc] — DEBT:defensive-narrow-payloads
    streamed = Response(content="".join(content_parts))
    try:
        # processor.post is a coroutine
        import asyncio

        await asyncio.create_task(processor.post(original_msg, streamed))  # type: ignore[misc] — DEBT:defensive-narrow-payloads
    except Exception:  # noqa: BLE001  — DEBT:boundary-broad-catch# top-level boundary
        log.warning("Processor post() failed (streaming)", exc_info=True)
