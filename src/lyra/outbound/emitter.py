"""OutboundEmitter — platform-agnostic streaming orchestrator.

Relocated from src/lyra/adapters/shared/_shared_streaming_emitter.py (issue #1279,
Phase 2 stage extraction). Renamed StreamingSession → OutboundEmitter.

Contains the orchestration algorithm (placeholder → debounced edits → final
delivery). Platform-specific behaviour is injected via OutboundFormatter Protocol.
State types live in _shared_streaming_state.py.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lyra.outbound.throttle import ThrottleCapability
    from lyra.transport.typing_publisher import TypingPublisher
    from lyra.transport.work_scope import WorkScope

from lyra.core.messaging import (
    RenderEvent,
    TextChunkRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)
from lyra.core.messaging.message import OutboundMessage
from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.outbound._emitter_run import _run_emitter
from lyra.outbound._streaming_state import StreamState
from lyra.outbound._tool_recap import ToolRecapAccumulator, format_recap_lines
from lyra.outbound.error_handler import OutboundErrorHandler
from lyra.outbound.formatter import OutboundFormatter
from lyra.outbound.throttle import STREAMING_EDIT_INTERVAL
from lyra.transport._result import Err
from lyra.transport.typing_publisher import is_typing_enabled

log = logging.getLogger(__name__)


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
    ``OutboundFormatter``. The session is single-use — create a new instance
    per outbound turn.
    """

    # Stage contract (ADR-073):
    #   Set EXCLUSIVELY by OutboundAdapterBase.send_streaming after _make_emitter
    #   returns; read by run() to construct the ToolRecapAccumulator. Concrete
    #   _make_emitter overrides MUST NOT assign this attribute — that would
    #   re-introduce per-platform wiring and re-create the target-axis-trap
    #   Phase B was designed to remove.
    tool_display_config: ToolDisplayConfig | None = None

    def __init__(
        self,
        formatter: OutboundFormatter,
        outbound: OutboundMessage | None,
        *,
        error_handler: OutboundErrorHandler | None = None,
        typing: "ThrottleCapability | None" = None,
        typing_scope_id: int | None = None,
    ) -> None:
        self._fmt = formatter
        self._outbound = outbound
        self._handler = error_handler or OutboundErrorHandler(get_msg=formatter.get_msg)
        self._typing = typing
        self._typing_scope_id = typing_scope_id
        self._st = StreamState()
        # Edit-debounce interval: ThrottleCapability owns it when injected, else
        # falls back to the module constant. Resolved at construction so callers
        # see a single coherent interval per session.
        self._edit_interval = (
            typing.edit_interval_s if typing is not None else STREAMING_EDIT_INTERVAL
        )
        self._trace_obj: Any | None = None
        self._last_recap_edit: float | None = None
        self._recap_done_emitted: bool = False
        self._tool_recap = ToolRecapAccumulator()
        # Pub/sub typing path (#1377) — injected by OutboundAdapterBase.send_streaming.
        self.typing_publisher: "TypingPublisher | None" = None
        self._work_scope: "WorkScope | None" = None

    async def _ensure_trace_obj(self) -> bool:
        """Lazily send the trace placeholder, caching it on self._trace_obj.

        Returns True if self._trace_obj is non-None after the call (success
        or already-set). False on send failure — caller should bail out.
        """
        if self._trace_obj is not None:
            return True
        result = await self._handler.guard(
            self._fmt.send_trace_placeholder,
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
            self._tool_recap.observe_start(event)
            if not await self._ensure_trace_obj():
                return
        elif isinstance(event, ToolCallArgsRenderEvent):
            self._tool_recap.observe_args(event)
        elif isinstance(event, ToolCallEndRenderEvent):
            self._tool_recap.observe_end(event)
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
            or (now - self._last_recap_edit) >= self._edit_interval
        ):
            lines = format_recap_lines(self._tool_recap, done=False)
            if lines:
                trace = self._trace_obj

                result = await self._handler.guard(
                    lambda t=trace, ls=lines: self._fmt.edit_tool_recap(t, ls, False),
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
                    or (now - self._st.last_intermediate_edit) >= self._edit_interval
                ):
                    display = self._st.istate.display()
                    result = await self._handler.guard(
                        lambda p=placeholder_obj, d=display: (
                            self._fmt.edit_placeholder_text(p, d)
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

    async def _cancel_typing(self) -> None:
        """Cancel typing via pub/sub or legacy ThrottleCapability."""
        if (
            is_typing_enabled()
            and self.typing_publisher is not None
            and self._work_scope is not None
        ):
            await self.typing_publisher.publish_ended(self._work_scope)
        elif self._typing is not None and self._typing_scope_id is not None:
            await self._typing.cancel_typing(self._typing_scope_id)

    async def _start_typing(self) -> None:
        """Start typing via pub/sub or legacy ThrottleCapability."""
        if (
            is_typing_enabled()
            and self.typing_publisher is not None
            and self._work_scope is not None
        ):
            await self.typing_publisher.publish_started(self._work_scope)
        elif self._typing is not None and self._typing_scope_id is not None:
            await self._typing.start_typing(self._typing_scope_id)

    async def run(self, events: AsyncIterator[RenderEvent]) -> None:
        """Run the full streaming lifecycle.

        Defers the placeholder until the first event arrives so that
        backend failures never leave an orphaned "…".  Re-raises
        stream errors after delivering the error message.
        """
        return await _run_emitter(self, events)
