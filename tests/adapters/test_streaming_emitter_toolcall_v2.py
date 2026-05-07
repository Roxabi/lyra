"""Slice 3 of #1096: shared streaming emitter must ignore ToolCall* v2 events.

Adapter parity story: Slice 3 emits ToolCall{Start,Args,End,Result} v2 events
alongside the v1 ``ToolSummaryRenderEvent`` (dual-emit, per umbrella resolved
decision 6). The existing v1-driven UX (Telegram edit-in-place, Discord embed)
must continue to drive what the user sees — adapters skip the v2 events for
parity until Slice 5 sunsets v1.

Discord opt-in inline args streaming is deferred to a follow-up issue
(``LYRA_DISCORD_TOOLCALL_STREAM_ARGS`` flag — not implemented this slice).
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from lyra.adapters.shared._shared_streaming import PlatformCallbacks, StreamingSession
from lyra.core.messaging.message import OutboundMessage
from lyra.core.messaging.render_events import (
    RenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
    ToolSummaryRenderEvent,
)


def _make_callbacks() -> PlatformCallbacks:
    return PlatformCallbacks(
        send_placeholder=AsyncMock(return_value=(object(), 42)),
        edit_placeholder_text=AsyncMock(),
        edit_placeholder_tool=AsyncMock(),
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
        # Must not raise — assert_never branch in dispatch must cover ToolCall*
        await session.run(
            _events(
                RunStartedRenderEvent(run_id="r1"),
                ToolCallStartRenderEvent(tool_call_id="t1", tool_name="Read"),
                ToolCallArgsRenderEvent(tool_call_id="t1", delta='{"k":'),
                ToolCallEndRenderEvent(tool_call_id="t1"),
                ToolCallResultRenderEvent(tool_call_id="t1", content="ok"),
                TextRenderEvent("bye", is_final=True),
                RunFinishedRenderEvent(run_id="r1"),
            )
        )

    async def test_toolcall_v2_does_not_call_edit_placeholder_tool(self) -> None:
        # Parity contract: v1 ``ToolSummaryRenderEvent`` drives the tool card edit;
        # v2 ToolCall* events are silently absorbed (no extra
        # ``edit_placeholder_tool`` invocation against the v2 events directly).
        cb = _make_callbacks()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(cb, outbound=outbound)
        await session.run(
            _events(
                RunStartedRenderEvent(run_id="r1"),
                ToolCallStartRenderEvent(tool_call_id="t1", tool_name="Read"),
                ToolCallEndRenderEvent(tool_call_id="t1"),
                TextRenderEvent("bye", is_final=True),
                RunFinishedRenderEvent(run_id="r1"),
            )
        )
        cb.edit_placeholder_tool.assert_not_called()  # type: ignore[attr-defined]

    async def test_v1_tool_summary_still_drives_tool_card(self) -> None:
        cb = _make_callbacks()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(cb, outbound=outbound)
        await session.run(
            _events(
                RunStartedRenderEvent(run_id="r1"),
                ToolCallStartRenderEvent(tool_call_id="t1", tool_name="Edit"),
                ToolSummaryRenderEvent(is_complete=True),
                ToolCallEndRenderEvent(tool_call_id="t1"),
                TextRenderEvent("done", is_final=True),
                RunFinishedRenderEvent(run_id="r1"),
            )
        )
        cb.edit_placeholder_tool.assert_called()  # type: ignore[attr-defined]
