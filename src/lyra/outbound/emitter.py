"""OutboundEmitter — platform-agnostic streaming orchestrator.

Relocated from src/lyra/adapters/shared/_shared_streaming_emitter.py (issue #1279,
Phase 2 stage extraction). Renamed StreamingSession → OutboundEmitter.
PlatformCallbacks dataclass kept here transitionally; split into
Formatter/Throttle/ErrorHandler stages in subsequent slices.

Contains the orchestration algorithm (placeholder → debounced edits → final
delivery) and the injectable PlatformCallbacks contract. State types live in
_shared_streaming_state.py.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, assert_never

# IMPORTANT: state + tool-recap imports are deferred to the BOTTOM of this file
# (after the class definitions) to break a circular import. Both
# lyra.adapters/__init__.py and lyra.adapters.shared/__init__.py do eager
# package-level imports of DiscordAdapter / StreamingSession / PlatformCallbacks
# that transitively re-enter this module through the
# _shared_streaming_emitter shim. If the imports were at the top of this file,
# Python would resolve them while emitter.py is still partially initialized,
# and the shim's `from lyra.outbound.emitter import OutboundEmitter` would
# fail with "partially initialized module". Deferring the import to the bottom
# of the file means OutboundEmitter is defined before the shared-state import
# fires, so the shim's lookup succeeds. (Issue #1279 keeps the state + recap
# files under lyra.adapters.shared/ per resolved spec Open Q 2.)
if TYPE_CHECKING:
    from lyra.adapters.shared._shared_streaming_state import (
        STREAMING_EDIT_INTERVAL,
        StreamState,
    )
    from lyra.adapters.shared._tool_recap import (
        ToolRecapAccumulator,
        format_recap_lines,
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
from lyra.outbound.error_handler import OutboundErrorHandler
from lyra.transport._result import Err

log = logging.getLogger(__name__)


async def _default_no_op_edit_tool_recap(
    trace_obj: Any,
    lines: list[str],
    done: bool,
) -> None:
    """Default no-op — adapters that haven't opted in render nothing."""
    del trace_obj, lines, done


async def _default_no_op_edit_reasoning(
    trace_obj: Any,
    event: ReasoningStartRenderEvent
    | ReasoningDeltaRenderEvent
    | ReasoningEndRenderEvent,
) -> None:
    """Default no-op — adapters that haven't opted in render nothing."""
    del trace_obj, event


@dataclass
class PlatformCallbacks:
    """Injectable platform callbacks for OutboundEmitter.

    Callbacks may raise; OutboundEmitter catches and handles exceptions
    internally except for stream errors which are re-raised from ``run()``.
    See adapters/CLAUDE.md for full field documentation.
    """

    send_placeholder: Callable[[], Awaitable[tuple[Any, int | None]]]
    edit_placeholder_text: Callable[[Any, str], Awaitable[None]]
    send_trace_placeholder: Callable[[], Awaitable[tuple[Any, int | None]]]
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
    edit_tool_recap: Callable[
        [Any, list[str], bool],
        Awaitable[None],
    ] = field(default=_default_no_op_edit_tool_recap)


async def _prepend(
    first: RenderEvent, rest: AsyncIterator[RenderEvent]
) -> AsyncIterator[RenderEvent]:
    """Yield *first*, then all items from *rest*."""
    yield first
    async for ev in rest:
        yield ev


class OutboundEmitter:
    """Platform-agnostic outbound streaming session.

    Composes formatter + throttle + error_handler stages.
    Relocated from src/lyra/adapters/shared/_shared_streaming_emitter.StreamingSession.

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
        *,
        error_handler: OutboundErrorHandler | None = None,
    ) -> None:
        self._cb = callbacks
        self._outbound = outbound
        self._handler = error_handler or OutboundErrorHandler(
            get_msg=callbacks.get_msg
        )
        self._st = StreamState()
        self._trace_obj: Any | None = None
        self._recap_accum = ToolRecapAccumulator()
        self._last_recap_edit: float | None = None
        self._recap_done_emitted: bool = False

    async def _ensure_trace_obj(self) -> bool:
        """Lazily send the trace placeholder, caching it on self._trace_obj.

        Returns True if self._trace_obj is non-None after the call (success
        or already-set). False on send failure — caller should bail out.
        """
        if self._trace_obj is not None:
            return True
        result = await self._handler.guard(
            self._cb.send_trace_placeholder,
            context="ensure_trace_obj",
        )
        if isinstance(result, Err):
            return False
        self._trace_obj, _ = result.value
        return self._trace_obj is not None

    async def _on_toolcall_v2(
        self,
        event: ToolCallStartRenderEvent
        | ToolCallArgsRenderEvent
        | ToolCallEndRenderEvent
        | ToolCallResultRenderEvent,
    ) -> None:
        """v2 ToolCall* dispatch — drives the recap card.

        Lazily sends the trace placeholder on the first Start (shared with
        reasoning rendering via ``self._trace_obj``). Accumulates per-event
        state and fires the platform's ``edit_tool_recap`` callback
        debounced at ``STREAMING_EDIT_INTERVAL``. The final ``done=True``
        edit fires from ``_deliver_final`` so it survives ungraceful stream
        ends.
        """
        self._st.had_tool_events = True

        if isinstance(event, ToolCallStartRenderEvent):
            self._recap_accum.observe_start(event)
            if not await self._ensure_trace_obj():
                return
        elif isinstance(event, ToolCallArgsRenderEvent):
            self._recap_accum.observe_args(event)
        elif isinstance(event, ToolCallEndRenderEvent):
            self._recap_accum.observe_end(event)
        else:
            # ToolCallResultRenderEvent — recap is input-only; nothing to do.
            return

        await self._maybe_emit_intermediate_recap()

    async def _maybe_emit_intermediate_recap(self) -> None:
        """Fire a debounced intermediate recap edit if conditions are met."""
        if self._trace_obj is None:
            return
        now = time.monotonic()
        if (
            self._last_recap_edit is None
            or (now - self._last_recap_edit) >= STREAMING_EDIT_INTERVAL
        ):
            lines = format_recap_lines(self._recap_accum, done=False)
            if lines:
                trace = self._trace_obj

                result = await self._handler.guard(
                    lambda t=trace, ls=lines: self._cb.edit_tool_recap(t, ls, False),
                    context="recap_intermediate_edit",
                )
                if isinstance(result, Err):
                    pass  # guard already logged; non-fatal
                self._last_recap_edit = now

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
                    display = self._st.istate.display()
                    result = await self._handler.guard(
                        lambda p=placeholder_obj, d=display: (
                            self._cb.edit_placeholder_text(p, d)
                        ),
                        context="intermediate_text_edit",
                    )
                    if isinstance(result, Err):
                        pass  # guard already logged; non-fatal
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
        result = await self._handler.guard(
            self._cb.send_placeholder,
            context="send_placeholder",
        )
        if isinstance(result, Err):
            self._cb.cancel_typing()
            return None
        placeholder_obj, reply_message_id = result.value
        if self._outbound is not None:
            self._outbound.metadata["reply_message_id"] = reply_message_id
        return placeholder_obj, reply_message_id

    async def _drain_fallback(self, events: AsyncIterator[RenderEvent]) -> None:
        """Drain remaining events, accumulate text, send via fallback callback."""
        parts: list[str] = []
        async for event in events:
            if isinstance(event, TextDeltaRenderEvent):
                parts.append(event.delta)
        fallback_text = "".join(parts) or self._cb.placeholder_text
        result = await self._handler.guard(
            lambda t=fallback_text: self._cb.send_fallback(t),
            context="drain_fallback",
        )
        if isinstance(result, Err):
            return
        fallback_message_id = result.value
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
                    # On Start, ensure the shared trace placeholder exists so
                    # reasoning and recap share a single placeholder object.
                    if isinstance(event, ReasoningStartRenderEvent):
                        await self._ensure_trace_obj()
                    await self._cb.edit_reasoning(self._trace_obj, event)
                    continue
                else:
                    assert_never(event)

        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch — terminal, migrated in S7
            self._st.stream_error = exc

    async def _deliver_text_chunks(
        self,
        placeholder_obj: Any,
        final_chunks: list[str],
    ) -> None:
        """Edit placeholder with first chunk, send overflow."""
        result = await self._handler.guard(
            lambda p=placeholder_obj, c=final_chunks[0]: (
                self._cb.edit_placeholder_text(p, c)
            ),
            context="deliver_final_edit",
        )
        if isinstance(result, Err):
            pass  # guard already logged; non-fatal
        for extra_chunk in final_chunks[1:]:
            result = await self._handler.guard(
                lambda c=extra_chunk: self._cb.send_message(c),
                context="deliver_overflow_chunk",
            )
            if isinstance(result, Err):
                pass  # guard already logged; non-fatal

    async def _deliver_final(
        self,
        placeholder_obj: Any,
    ) -> None:
        """Deliver the final message after the event loop.

        Terminal invariant: the placeholder must never be left as a bare
        "…".  If no text was produced and no stream error was raised, edit
        it to a generic error so the user always sees a final state.
        """
        # Best-effort final recap edit. Fires for graceful AND ungraceful ends
        # (no RunFinished, no RunError) — guarantees the placeholder never
        # sticks at "🔧 Working…".
        if (
            self._st.had_tool_events
            and self._trace_obj is not None
            and not self._recap_done_emitted
        ):
            self._recap_done_emitted = True
            lines = format_recap_lines(self._recap_accum, done=True)
            if lines:
                trace = self._trace_obj
                result = await self._handler.guard(
                    lambda t=trace, ls=lines: self._cb.edit_tool_recap(t, ls, True),
                    context="recap_final_edit",
                )
                if isinstance(result, Err):
                    pass  # guard already logged; non-fatal

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
            self._handler.classify_stream_error(
                self._st.stream_error,
                had_tool_events=self._st.had_tool_events,
                final_text=self._st.final_text,
            )
            or GENERIC_ERROR_REPLY
        )
        result = await self._handler.guard(
            lambda p=placeholder_obj, t=error_text: (
                self._cb.edit_placeholder_text(p, t)
            ),
            context="error_edit",
        )
        if isinstance(result, Err):
            pass  # guard already logged; non-fatal

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
        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch — terminal, migrated in S7
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


# Deferred imports — see the explanatory comment at the top of the file.
# These run AFTER OutboundEmitter is fully defined, so the shim's lookup of
# OutboundEmitter (triggered transitively by lyra.adapters.__init__) finds a
# fully initialized class instead of a partially loaded module.
from lyra.adapters.shared._shared_streaming_state import (  # noqa: E402
    STREAMING_EDIT_INTERVAL,
    StreamState,
)
from lyra.adapters.shared._tool_recap import (  # noqa: E402
    ToolRecapAccumulator,
    format_recap_lines,
)
