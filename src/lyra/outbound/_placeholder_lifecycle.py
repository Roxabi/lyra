"""Placeholder lifecycle helpers — extracted from OutboundEmitter."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lyra.outbound.emitter import OutboundEmitter

from lyra.core.messaging import RenderEvent, TextDeltaRenderEvent
from lyra.core.messaging.message import GENERIC_ERROR_REPLY
from lyra.outbound._tool_recap import format_recap_lines
from lyra.transport._result import Err

log = logging.getLogger(__name__)


async def _send_placeholder(
    emitter: "OutboundEmitter",
) -> tuple[Any, int | None] | None:
    """Send placeholder; record reply_message_id on outbound.

    Returns None on failure (caller should call _handle_typing_tail and return).
    Returns (placeholder_obj, reply_message_id) on success.
    """
    result = await emitter._handler.guard(
        emitter._fmt.send_placeholder,
        context="send_placeholder",
    )
    if isinstance(result, Err):
        await emitter._cancel_typing()
        return None
    placeholder_obj, reply_message_id = result.value
    if emitter._outbound is not None:
        emitter._outbound.metadata["reply_message_id"] = reply_message_id
    return placeholder_obj, reply_message_id


async def _drain_fallback(
    emitter: "OutboundEmitter", events: AsyncIterator[RenderEvent]
) -> None:
    """Drain events, accumulate text, send via fallback."""
    parts: list[str] = []
    async for event in events:
        if isinstance(event, TextDeltaRenderEvent):
            parts.append(event.delta)
    fallback_text = "".join(parts) or emitter._fmt.placeholder_text()
    result = await emitter._handler.guard(
        lambda t=fallback_text: emitter._fmt.send_fallback(t),
        context="drain_fallback",
    )
    if isinstance(result, Err):
        return
    fallback_message_id = result.value
    if emitter._outbound is not None and fallback_message_id is not None:
        emitter._outbound.metadata["reply_message_id"] = fallback_message_id


async def _deliver_text_chunks(
    emitter: "OutboundEmitter", placeholder_obj: Any, final_chunks: list[str]
) -> None:
    """Edit placeholder with first chunk, send overflow."""
    result = await emitter._handler.guard(
        lambda p=placeholder_obj, c=final_chunks[0]: emitter._fmt.edit_placeholder_text(
            p, c
        ),
        context="deliver_final_edit",
    )
    if isinstance(result, Err):
        pass  # guard already logged; non-fatal
    for extra_chunk in final_chunks[1:]:
        result = await emitter._handler.guard(
            lambda c=extra_chunk: emitter._fmt.send_message(c),
            context="deliver_overflow_chunk",
        )
        if isinstance(result, Err):
            pass  # guard already logged; non-fatal


async def _deliver_final(emitter: "OutboundEmitter", placeholder_obj: Any) -> None:
    """Deliver final message after the event loop.

    Terminal invariant: the placeholder must never be left as a bare "…".
    """
    # Best-effort final recap edit. Fires for graceful AND ungraceful ends
    # (no RunFinished, no RunError) — guarantees the placeholder never
    # sticks at "🔧 Working…".
    if (
        emitter._st.had_tool_events
        and emitter._trace_obj is not None
        and not emitter._recap_done_emitted
    ):
        emitter._recap_done_emitted = True
        lines = format_recap_lines(emitter._tool_recap, done=True)
        if lines:
            trace = emitter._trace_obj
            result = await emitter._handler.guard(
                lambda t=trace, ls=lines: emitter._fmt.edit_tool_recap(t, ls, True),
                context="recap_final_edit",
            )
            if isinstance(result, Err):
                pass  # guard already logged; non-fatal

    display_text = emitter._st.build_display_text(emitter._fmt.get_msg)
    chunks = emitter._fmt.chunk(display_text) if display_text else []
    if chunks:
        await _deliver_text_chunks(emitter, placeholder_obj, chunks)
        return

    # No deliverable content — surface a descriptive error rather than "…".
    log.warning(
        "streaming turn ended with no display text (final_text=%r stream_error=%r)",
        emitter._st.final_text,
        emitter._st.stream_error,
    )
    error_text = (
        emitter._handler.classify_stream_error(
            emitter._st.stream_error,
            had_tool_events=emitter._st.had_tool_events,
            final_text=emitter._st.final_text,
        )
        or GENERIC_ERROR_REPLY
    )
    result = await emitter._handler.guard(
        lambda p=placeholder_obj, t=error_text: emitter._fmt.edit_placeholder_text(
            p, t
        ),
        context="error_edit",
    )
    if isinstance(result, Err):
        pass  # guard already logged; non-fatal


async def _handle_typing_tail(emitter: "OutboundEmitter") -> None:
    """Start/cancel typing based on whether the turn is intermediate."""
    if emitter._outbound is not None and emitter._outbound.intermediate:
        await emitter._start_typing()
    else:
        await emitter._cancel_typing()
