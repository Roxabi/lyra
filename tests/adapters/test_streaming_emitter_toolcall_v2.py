"""Slice 3 of #1096: shared streaming emitter must handle ToolCall* v2 events.

After Slice 5 / #1192 v1 cutover: TextRenderEvent and ToolSummaryRenderEvent are
gone. ToolCall* events are absorbed by the dispatch ladder silently.
Text is conveyed via the v2 TextStart/Delta/End triplet only.
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from lyra.core.messaging.message import OutboundMessage
from lyra.core.messaging.render_events import (
    RenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)
from lyra.outbound.emitter import OutboundEmitter as StreamingSession
from lyra.outbound.emitter import PlatformCallbacks


def _make_callbacks() -> PlatformCallbacks:
    return PlatformCallbacks(
        send_placeholder=AsyncMock(return_value=(object(), 42)),
        edit_placeholder_text=AsyncMock(),
        send_trace_placeholder=AsyncMock(return_value=(object(), 42)),
        send_message=AsyncMock(return_value=99),
        send_fallback=AsyncMock(return_value=77),
        chunk_text=MagicMock(side_effect=lambda t: [t] if t else []),
        start_typing=MagicMock(),
        cancel_typing=MagicMock(),
        get_msg=MagicMock(side_effect=lambda key, fallback: fallback),
        placeholder_text="…",
    )


async def _events(*evts: RenderEvent) -> AsyncIterator[RenderEvent]:
    for e in evts:
        yield e


class TestSharedEmitterIgnoresToolCallV2:
    """ToolCall* v2 events must not raise, must not crash dispatch, must not edit."""

    async def test_toolcall_v2_events_do_not_raise(self) -> None:
        cb = _make_callbacks()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(cb, outbound=outbound)
        # Must not raise — dispatch ladder covers ToolCall*; v2 text triplet used
        await session.run(
            _events(
                RunStartedRenderEvent(run_id="r1"),
                ToolCallStartRenderEvent(tool_call_id="t1", tool_name="Read"),
                ToolCallArgsRenderEvent(tool_call_id="t1", delta='{"k":'),
                ToolCallEndRenderEvent(tool_call_id="t1"),
                ToolCallResultRenderEvent(tool_call_id="t1", content="ok"),
                TextStartRenderEvent(message_id="msg-1"),
                TextDeltaRenderEvent(message_id="msg-1", delta="bye"),
                TextEndRenderEvent(message_id="msg-1"),
                RunFinishedRenderEvent(run_id="r1"),
            )
        )

    async def test_subclass_override_receives_toolcall_v2(self) -> None:
        """PL1 (#1100 review): _on_toolcall_v2 override seam is invoked.

        Ensures the dispatch actually awaits the method on the session, so
        platform subclasses can render richer when they migrate off v1.
        """
        captured: list[RenderEvent] = []

        class _RecordingSession(StreamingSession):
            async def _on_toolcall_v2(
                self,
                event: ToolCallStartRenderEvent
                | ToolCallArgsRenderEvent
                | ToolCallEndRenderEvent
                | ToolCallResultRenderEvent,
            ) -> None:
                captured.append(event)

        cb = _make_callbacks()
        outbound = OutboundMessage.from_text("hi")
        session = _RecordingSession(cb, outbound=outbound)
        await session.run(
            _events(
                RunStartedRenderEvent(run_id="r1"),
                ToolCallStartRenderEvent(tool_call_id="t1", tool_name="Read"),
                ToolCallArgsRenderEvent(tool_call_id="t1", delta='{"k":'),
                ToolCallEndRenderEvent(tool_call_id="t1"),
                ToolCallResultRenderEvent(tool_call_id="t1", content="ok"),
                TextStartRenderEvent(message_id="msg-1"),
                TextDeltaRenderEvent(message_id="msg-1", delta="bye"),
                TextEndRenderEvent(message_id="msg-1"),
                RunFinishedRenderEvent(run_id="r1"),
            )
        )
        # All 4 ToolCall* event types must reach the override.
        types = [type(e).__name__ for e in captured]
        assert types == [
            "ToolCallStartRenderEvent",
            "ToolCallArgsRenderEvent",
            "ToolCallEndRenderEvent",
            "ToolCallResultRenderEvent",
        ]
