"""Shared streaming session for channel adapters.

Extracts the edit-in-place streaming algorithm from Telegram and Discord outbound
adapters into a single ``StreamingSession`` class. Platform-specific behaviour
(message API calls, text rendering) is injected via ``PlatformCallbacks``.

This is Slice 2 of #468 — centralising streaming adapters.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from lyra.adapters._shared import (
    STREAMING_EDIT_INTERVAL,
    StreamState,
    classify_stream_error,
)
from lyra.core.message import GENERIC_ERROR_REPLY, OutboundMessage
from lyra.core.render_events import RenderEvent, TextRenderEvent, ToolSummaryRenderEvent
from lyra.core.tool_recap_format import format_tool_lines

log = logging.getLogger(__name__)


async def _prepend(
    first: RenderEvent, rest: AsyncIterator[RenderEvent]
) -> AsyncIterator[RenderEvent]:
    """Yield *first*, then all items from *rest*."""
    yield first
    async for ev in rest:
        yield ev


@dataclass
class PlatformCallbacks:
    """Injectable platform callbacks for StreamingSession.

    Callbacks may raise; StreamingSession catches and handles exceptions
    internally except for stream errors which are re-raised from ``run()``.
    See adapters/CLAUDE.md for full field documentation.
    """

    send_placeholder: Callable[[], Awaitable[tuple[Any, int | None]]]
    edit_placeholder_text: Callable[[Any, str], Awaitable[None]]
    edit_placeholder_tool: Callable[[Any, ToolSummaryRenderEvent, str], Awaitable[None]]
    send_message: Callable[[str], Awaitable[int | None]]
    send_fallback: Callable[[str], Awaitable[int | None]]
    chunk_text: Callable[[str], list[str]]
    start_typing: Callable[[], None]
    cancel_typing: Callable[[], None]
    get_msg: Callable[[str, str], str]
    placeholder_text: str
    guard_tool_on_intermediate: bool = True


class StreamingSession:
    """Platform-agnostic streaming session.

    Orchestrates the streaming lifecycle:
      1. Send placeholder
      2. Edit placeholder on each event (debounced)
      3. Deliver final text (edit placeholder or send new message for tool turns)
      4. Manage typing indicator tail

    Platform-specific behaviour (API calls, text formatting) is injected via
    ``PlatformCallbacks``. The session is single-use — create a new instance
    per outbound turn.
    """

    def __init__(
        self,
        callbacks: PlatformCallbacks,
        outbound: OutboundMessage | None,
    ) -> None:
        self._cb = callbacks
        self._outbound = outbound
        self._st = StreamState()

    async def _send_placeholder(self) -> tuple[Any, int | None] | None:
        """Send the placeholder and record reply_message_id on outbound.

        On failure: cancels typing, drains events accumulating text, sends fallback.
        Returns None on failure (caller should call _handle_typing_tail and return).
        Returns (placeholder_obj, reply_message_id) on success.
        """
        try:
            placeholder_obj, reply_message_id = await self._cb.send_placeholder()
            if self._outbound is not None:
                self._outbound.metadata["reply_message_id"] = reply_message_id
            return placeholder_obj, reply_message_id
        except Exception:
            self._cb.cancel_typing()
            log.exception("Failed to send placeholder — falling back to non-streaming")
            return None

    async def _drain_fallback(self, events: AsyncIterator[RenderEvent]) -> None:
        """Drain remaining events, accumulate text, send via fallback callback."""
        parts: list[str] = []
        async for event in events:
            if isinstance(event, TextRenderEvent):
                parts.append(event.text)
        fallback_text = "".join(parts) or self._cb.placeholder_text
        try:
            fallback_message_id = await self._cb.send_fallback(fallback_text)
        except Exception:
            log.exception("Fallback send failed — message lost")
            return
        if self._outbound is not None and fallback_message_id is not None:
            self._outbound.metadata["reply_message_id"] = fallback_message_id

    async def _run_event_loop(
        self,
        events: AsyncIterator[RenderEvent],
        placeholder_obj: Any,
    ) -> None:
        """Iterate over events, updating the placeholder with debounced edits."""
        try:
            async for event in events:
                if isinstance(event, ToolSummaryRenderEvent):
                    self._st.had_tool_events = True
                    header = "🔧 Done ✅" if event.is_complete else "🔧 Working…"
                    body = "\n".join(format_tool_lines(event))
                    summary = f"{header}\n{body}".strip() if body else header
                    self._st.istate.set_tool_summary(summary)

                    # Guard: on Discord, don't overwrite intermediate text already
                    # visible in the placeholder (tool summary lives in a separate
                    # embed). On Telegram, tool summary is combined with intermediate
                    # text via IntermediateTextState.display(combine_recap=True).
                    if not (
                        self._cb.guard_tool_on_intermediate
                        and self._st.istate.has_intermediate_text
                    ):
                        now = time.monotonic()
                        if (
                            event.is_complete
                            or self._st.last_tool_edit is None
                            or (now - self._st.last_tool_edit)
                            >= STREAMING_EDIT_INTERVAL
                        ):
                            display_text = self._st.istate.display()
                            try:
                                await self._cb.edit_placeholder_tool(
                                    placeholder_obj, event, display_text
                                )
                            except Exception as edit_exc:
                                log.debug("Tool summary edit skipped: %s", edit_exc)
                            self._st.last_tool_edit = now

                else:  # TextRenderEvent
                    if event.is_final:
                        self._st.on_final_text(event)
                    else:
                        self._st.istate.append(event.text)
                        now = time.monotonic()
                        if (
                            self._st.last_intermediate_edit is None
                            or (now - self._st.last_intermediate_edit)
                            >= STREAMING_EDIT_INTERVAL
                        ):
                            try:
                                await self._cb.edit_placeholder_text(
                                    placeholder_obj, self._st.istate.display()
                                )
                            except Exception as edit_exc:
                                log.debug(
                                    "Intermediate text edit skipped: %s", edit_exc
                                )
                            self._st.last_intermediate_edit = now

        except Exception as exc:
            self._st.stream_error = exc
            log.exception("Stream interrupted")

    async def _deliver_tool_chunks(
        self,
        final_chunks: list[str],
    ) -> None:
        """Send final text as new messages (tool-using turns)."""
        last_msg_id: int | None = None
        for chunk in final_chunks:
            try:
                last_msg_id = await self._cb.send_message(chunk)
            except Exception:
                log.exception("Failed to send final text chunk")
        if self._outbound is not None and last_msg_id is not None:
            self._outbound.metadata["reply_message_id"] = last_msg_id

    async def _deliver_text_chunks(
        self,
        placeholder_obj: Any,
        final_chunks: list[str],
    ) -> None:
        """Edit placeholder with first chunk, send overflow."""
        try:
            await self._cb.edit_placeholder_text(
                placeholder_obj,
                final_chunks[0],
            )
        except Exception:
            log.exception("Final edit failed")
        for extra_chunk in final_chunks[1:]:
            try:
                await self._cb.send_message(extra_chunk)
            except Exception:
                log.exception("Failed to send overflow chunk")

    async def _deliver_final(
        self,
        placeholder_obj: Any,
    ) -> None:
        """Deliver the final message after the event loop.

        Terminal invariant: the placeholder must never be left as a bare
        "…".  If no text was produced and no stream error was raised, edit
        it to a generic error so the user always sees a final state.
        """
        display_text = self._st.build_display_text(self._cb.get_msg)
        chunks = self._cb.chunk_text(display_text) if display_text else []
        if chunks:
            if self._st.had_tool_events:
                await self._deliver_tool_chunks(chunks)
            else:
                await self._deliver_text_chunks(placeholder_obj, chunks)
            return

        # No deliverable content — surface a descriptive error rather than "…".
        log.warning(
            "streaming turn ended with no display text"
            " (final_text=%r stream_error=%r had_tool_events=%s)",
            self._st.final_text,
            self._st.stream_error,
            self._st.had_tool_events,
        )
        error_text = (
            classify_stream_error(
                self._st.stream_error,
                had_tool_events=self._st.had_tool_events,
                final_text=self._st.final_text,
                msg_fn=self._cb.get_msg,
            )
            or GENERIC_ERROR_REPLY
        )
        try:
            await self._cb.edit_placeholder_text(placeholder_obj, error_text)
        except Exception as edit_exc:
            log.debug("Error edit skipped: %s", edit_exc)

    def _handle_typing_tail(self) -> None:
        """Start or cancel typing based on whether the turn is intermediate."""
        if self._outbound is not None and self._outbound.intermediate:
            self._cb.start_typing()
        else:
            self._cb.cancel_typing()

    async def run(self, events: AsyncIterator[RenderEvent]) -> None:
        """Run the full streaming lifecycle.

        Defers the placeholder until the first event arrives so that
        backend failures never leave an orphaned "…".  Re-raises
        stream errors after delivering the error message.
        """
        # Peek: empty stream → fallback, no placeholder.
        first_event: RenderEvent | None = None
        peek_error: Exception | None = None
        try:
            first_event = await events.__anext__()
        except StopAsyncIteration:
            pass
        except Exception as exc:
            peek_error = exc

        if first_event is None and peek_error is None:
            await self._drain_fallback(events)
            self._handle_typing_tail()
            return
        if peek_error is not None:
            self._st.stream_error = peek_error
            result = await self._send_placeholder()
            if result is not None:
                await self._deliver_final(result[0])
            self._handle_typing_tail()
            raise peek_error

        assert first_event is not None  # narrowed above
        result = await self._send_placeholder()
        full = _prepend(first_event, events)
        if result is None:
            await self._drain_fallback(full)
            self._handle_typing_tail()
            return
        placeholder_obj, _ = result
        await self._run_event_loop(full, placeholder_obj)
        await self._deliver_final(placeholder_obj)
        self._handle_typing_tail()
        if self._st.stream_error is not None:
            raise self._st.stream_error
