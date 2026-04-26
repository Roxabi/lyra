"""Streaming helpers extracted from pool_processor_exec (issue #753).

Contains the async generator capture wrapper and streaming turn-logging callback.
"""

from __future__ import annotations

import collections.abc
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..messaging.message import InboundMessage
    from .pool import Pool

from ..messaging.message import OutboundMessage, Response
from ..messaging.render_events import RenderEvent, TextRenderEvent

log = logging.getLogger(__name__)


def build_streaming_capture(
    result_iter: collections.abc.AsyncIterator[RenderEvent],
    content_parts: list[str],
    pool: Pool,
    stream_done_event: object | None,
    emit_tool_recap: bool,
) -> collections.abc.AsyncGenerator[RenderEvent, None]:
    """Build an async generator that captures TextRenderEvent content.

    Wraps the result iterator to collect text content for turn logging while
    forwarding all events. Filters out ToolSummaryRenderEvent when recap is disabled.
    """

    async def _capture() -> collections.abc.AsyncGenerator[RenderEvent, None]:
        try:
            async for event in result_iter:
                if isinstance(event, TextRenderEvent):
                    content_parts.append(event.text)
                    if event.is_error:
                        pool._last_turn_had_backend_error = True
                elif not emit_tool_recap:
                    # ToolSummaryRenderEvent — suppress when recap is disabled
                    continue
                yield event
        finally:
            _aclose = getattr(result_iter, "aclose", None)
            if callable(_aclose):
                await _aclose()  # type: ignore[misc]
            if stream_done_event is not None:
                stream_done_event.set()  # type: ignore[misc]

    return _capture()


def build_streaming_turn_logger(  # noqa: PLR0913 — internal helper, params bundled for streaming context
    pool: Pool,
    result_iter_for_sid: collections.abc.AsyncIterator[RenderEvent],
    original_msg: InboundMessage,
    platform: str,
    user_id: str,
    content_parts: list[str],
) -> tuple[OutboundMessage, Callable[[OutboundMessage], Awaitable[None]]]:
    """Build OutboundMessage with turn-logging callback for streaming responses.

    Returns:
        Tuple of (outbound_msg, log_callback) where callback should be attached
        to outbound.metadata["_on_dispatched"].
    """
    outbound = OutboundMessage.from_text("")

    async def _log_streaming_turn(outbound_msg: OutboundMessage) -> None:
        # Clear inflight reference once streaming is fully delivered.
        if pool._inflight_stream_outbound is outbound_msg:
            pool._inflight_stream_outbound = None
        # Propagate CLI session_id from the (now-consumed) iterator.
        _stream_sid = getattr(result_iter_for_sid, "session_id", None)
        if _stream_sid and pool.session_id != _stream_sid:
            await pool._observer.end_session_async(pool.session_id)
            pool.session_id = _stream_sid
        await pool._observer.session_update_async(original_msg)
        _reply_id = outbound_msg.metadata.get("reply_message_id")
        await pool._observer.log_turn_async(
            role="assistant",
            platform=platform,
            user_id=user_id,
            content="".join(content_parts),
            reply_message_id=(str(_reply_id) if _reply_id is not None else None),
        )
        # Index assistant turn for reply-to session routing (#341).
        await pool._observer.index_turn_async(
            str(_reply_id) if _reply_id is not None else None,
            session_id=pool.session_id,
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
    await stream_done_event.wait()  # type: ignore[misc]
    streamed = Response(content="".join(content_parts))
    try:
        # processor.post is a coroutine
        import asyncio

        await asyncio.create_task(processor.post(original_msg, streamed))  # type: ignore[misc]
    except Exception:
        log.warning("Processor post() failed (streaming)", exc_info=True)
