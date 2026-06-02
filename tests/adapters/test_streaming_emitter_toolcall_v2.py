"""Slice 3 of #1096: shared streaming emitter must handle ToolCall* v2 events.

After Slice 5 / #1192 v1 cutover: TextRenderEvent and ToolSummaryRenderEvent are
gone. ToolCall* events are absorbed by the dispatch ladder silently.
Text is conveyed via the v2 TextStart/Delta/End triplet only.

Migrated in S7 (#1501): PlatformCallbacks replaced with MagicMock OutboundFormatter.
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from factory.core.messaging.message import OutboundMessage
from factory.core.messaging.render_events import (
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
from factory.outbound.emitter import OutboundEmitter as StreamingSession


def _make_formatter(**overrides) -> MagicMock:
    """Build a mock OutboundFormatter with AsyncMock/MagicMock defaults."""
    fmt = MagicMock()
    fmt.placeholder_text = MagicMock(return_value="…")
    fmt.chunk = MagicMock(side_effect=lambda t: [t] if t else [])
    fmt.get_msg = MagicMock(side_effect=lambda key, fallback: fallback)
    fmt.send_placeholder = AsyncMock(return_value=(object(), 42))
    fmt.edit_placeholder_text = AsyncMock()
    fmt.send_trace_placeholder = AsyncMock(return_value=(object(), 42))
    fmt.send_message = AsyncMock(return_value=99)
    fmt.send_fallback = AsyncMock(return_value=77)
    fmt.edit_reasoning = AsyncMock()
    fmt.edit_tool_recap = AsyncMock()
    for k, v in overrides.items():
        setattr(fmt, k, v)
    return fmt


async def _events(*evts: RenderEvent) -> AsyncIterator[RenderEvent]:
    for e in evts:
        yield e


class TestSharedEmitterIgnoresToolCallV2:
    """ToolCall* v2 events must not raise, must not crash dispatch, must not edit."""

    async def test_toolcall_v2_events_do_not_raise(self) -> None:
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)
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

        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = _RecordingSession(fmt, outbound=outbound)
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
