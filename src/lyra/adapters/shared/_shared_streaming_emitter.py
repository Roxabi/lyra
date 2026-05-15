"""Streaming session and platform callbacks — StreamingSession, PlatformCallbacks.

Extracted from _shared_streaming.py (Issue #760).  Contains the orchestration
algorithm (placeholder → debounced edits → final delivery) and the injectable
PlatformCallbacks contract.  State types live in _shared_streaming_state.py.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, assert_never

from lyra.adapters.shared._shared_streaming_state import (
    STREAMING_EDIT_INTERVAL,
    StreamState,
    classify_stream_error,
)
from lyra.core.messaging import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
    RenderEvent,
    RunErrorRenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextChunkRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)
from lyra.core.messaging.message import GENERIC_ERROR_REPLY, OutboundMessage

log = logging.getLogger(__name__)


async def _default_no_op_edit_reasoning(
    trace_obj: Any,
    event: ReasoningStartRenderEvent
    | ReasoningDeltaRenderEvent
    | ReasoningEndRenderEvent,
) -> None:
    """Default no-op — adapters that haven't opted in render nothing."""


@dataclass
class PlatformCallbacks:
    """Injectable platform callbacks for StreamingSession.

    Callbacks may raise; StreamingSession catches and handles exceptions
    internally except for stream errors which are re-raised from ``run()``.
    See adapters/CLAUDE.md for full field documentation.
    """

    send_placeholder: Callable[[], Awaitable[tuple[Any, int | None]]]
    edit_placeholder_text: Callable[[Any, str], Awaitable[None]]
    send_trace_placeholder: Callable[[], Awaitable[tuple[Any, int | None]]]
    edit_trace: Callable[[Any, Any], Awaitable[None]]
    send_message: Callable[[str], Awaitable[int | None]]
    send_fallback: Callable[[str], Awaitable[int | None]]
    chunk_text: Callable[[str], list[str]]
    start_typing: Callable[[], None]
    cancel_typing: Callable[[], None]
    get_msg: Callable[[str, str], str]
    placeholder_text: str
    edit_reasoning: Callable[
        [
            Any,
            ReasoningStartRenderEvent
            | ReasoningDeltaRenderEvent
            | ReasoningEndRenderEvent,
        ],
        Awaitable[None],
    ] = field(default=_default_no_op_edit_reasoning)


async def _prepend(
    first: RenderEvent, rest: AsyncIterator[RenderEvent]
) -> AsyncIterator[RenderEvent]:
    """Yield *first*, then all items from *rest*."""
    yield first
    async for ev in rest:
        yield ev


class StreamingSession:
    """Platform-agnostic streaming session.

    Orchestrates the streaming lifecycle:
      1. Send response placeholder
      2. Edit response placeholder on each text event (debounced)
      3. On first tool event: lazily send trace placeholder, edit it with tool activity
      4. Deliver final text by editing the response placeholder in-place (always)
      5. Manage typing indicator tail

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
        self._trace_obj: Any | None = None

    async def _on_toolcall_v2(
        self,
        event: ToolCallStartRenderEvent
        | ToolCallArgsRenderEvent
        | ToolCallEndRenderEvent
        | ToolCallResultRenderEvent,
    ) -> None:
        """v2 ToolCall* dispatch sink. Slice 3 (#1100) introduced this.

        Records that a tool event was observed (drives the tool-only fallback
        message in ``classify_stream_error``). Platform subclasses may override
        to render richer tool activity; they should call ``super()`` to preserve
        the flag.
        """
        self._st.had_tool_events = True
        return None

    async def _on_text_v2(
        self,
        event: TextStartRenderEvent
        | TextDeltaRenderEvent
        | TextEndRenderEvent
        | TextChunkRenderEvent,
        placeholder_obj: Any = None,
    ) -> None:
        """v2 Text* dispatch — drives streaming edit-in-place (post-Slice-5 / #1192).

        ``TextDeltaRenderEvent`` deltas accumulate in ``_st.istate`` and trigger
        debounced placeholder edits. ``TextEndRenderEvent`` captures the
        accumulated text as the final text (no separate TextRenderEvent in v2).
        ``TextStartRenderEvent`` and ``TextChunkRenderEvent`` are no-ops here.
        """
        if isinstance(event, TextDeltaRenderEvent):
            self._st.istate.append(event.delta)
            if placeholder_obj is not None:
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
                    except Exception as edit_exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
                        log.debug("Intermediate text edit skipped: %s", edit_exc)
                    self._st.last_intermediate_edit = now
        elif isinstance(event, TextEndRenderEvent):
            # TextEnd closes the text block; accumulated istate text is the final text.
            # The error-turn flag is applied at delivery time in
            # build_display_text() — NOT here — because RunErrorRenderEvent
            # arrives AFTER TextEnd in stream_processor's production order
            # (post-finally emission). Capturing is_error here would race the
            # RunError signal and silently drop the ``❌`` prefix.
            if self._st.istate.text:
                final = self._st.istate.text
                if final.startswith("⏳ "):
                    final = final[2:]
                self._st.set_final_text(final)

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
            if isinstance(event, TextDeltaRenderEvent):
                parts.append(event.delta)
        fallback_text = "".join(parts) or self._cb.placeholder_text
        try:
            fallback_message_id = await self._cb.send_fallback(fallback_text)
        except Exception:
            log.exception("Fallback send failed — message lost")
            return
        if self._outbound is not None and fallback_message_id is not None:
            self._outbound.metadata["reply_message_id"] = fallback_message_id

    async def _run_event_loop(  # noqa: C901 — DEBT:wiring-bootstrap-deps — v1+v2 dispatch ladder
        self,
        events: AsyncIterator[RenderEvent],
        placeholder_obj: Any,
    ) -> None:
        """Iterate over events, updating the placeholder with debounced edits."""
        try:
            async for event in events:
                if isinstance(
                    event,
                    RunStartedRenderEvent
                    | RunFinishedRenderEvent
                    | RunErrorRenderEvent,
                ):
                    # Slice 1 (#1098): Run lifecycle events are pure additive
                    # surface — adapters initially ignore (no UX). Future slices
                    # may render banners or expose run_id in observability.
                    # RunErrorRenderEvent flags the turn as error so the
                    # subsequent TextEnd (if any) sets is_error_turn=True,
                    # producing the ``❌`` prefix on the final rendered text.
                    if isinstance(event, RunErrorRenderEvent):
                        self._st.is_error_pending = True
                    continue

                if isinstance(
                    event,
                    ToolCallStartRenderEvent
                    | ToolCallArgsRenderEvent
                    | ToolCallEndRenderEvent
                    | ToolCallResultRenderEvent,
                ):
                    # Slice 3 (#1100) / Slice 5 (#1192): ToolCall* lifecycle
                    # events. v1 ToolSummaryRenderEvent removed; platform
                    # subclasses override _on_toolcall_v2 for richer rendering.
                    await self._on_toolcall_v2(event)
                    continue

                if isinstance(  # pyright: ignore[reportUnnecessaryIsInstance] — DEBT:defensive-narrow-payloads
                    event,
                    TextStartRenderEvent
                    | TextDeltaRenderEvent
                    | TextEndRenderEvent
                    | TextChunkRenderEvent,
                ):
                    await self._on_text_v2(event, placeholder_obj)
                    continue

                if isinstance(  # pyright: ignore[reportUnnecessaryIsInstance] — DEBT:defensive-narrow-payloads
                    event,
                    ReasoningStartRenderEvent
                    | ReasoningDeltaRenderEvent
                    | ReasoningEndRenderEvent,
                ):
                    # Slice 4 (#1101): typed reasoning events. Routed through
                    # PlatformCallbacks.edit_reasoning (see T9.5). Default
                    # callback is no-op; adapters override via OutboundAdapterBase.
                    await self._cb.edit_reasoning(self._trace_obj, event)
                    continue
                else:
                    assert_never(event)

        except Exception as exc:
            self._st.stream_error = exc
            log.exception("Stream interrupted")

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
            await self._deliver_text_chunks(placeholder_obj, chunks)
            return

        # No deliverable content — surface a descriptive error rather than "…".
        log.warning(
            "streaming turn ended with no display text (final_text=%r stream_error=%r)",
            self._st.final_text,
            self._st.stream_error,
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
        except Exception as edit_exc:  # noqa: BLE001  — DEBT:boundary-broad-catch# streaming edit: any send failure is non-fatal
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
        except Exception as exc:  # noqa: BLE001  — DEBT:boundary-broad-catch# streaming edit: any send failure is non-fatal
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
