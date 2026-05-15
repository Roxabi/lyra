from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.adapters.shared._shared import (
    IntermediateTextState,
    chunk_text,
    format_tool_summary_header,
    send_with_retry,
)
from lyra.adapters.shared._shared_streaming import PlatformCallbacks, StreamingSession
from lyra.core.messaging.message import OutboundMessage
from lyra.core.messaging.render_events import (
    RenderEvent,
    TextChunkRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextRenderEvent,
    TextStartRenderEvent,
    ToolSummaryRenderEvent,
)

# ---------------------------------------------------------------------------
# Helpers shared by v2 Text dispatch tests
# ---------------------------------------------------------------------------


def _make_callbacks() -> PlatformCallbacks:
    return PlatformCallbacks(
        send_placeholder=AsyncMock(return_value=(object(), 42)),
        edit_placeholder_text=AsyncMock(),
        send_trace_placeholder=AsyncMock(return_value=(object(), 42)),
        edit_trace=AsyncMock(),
        send_message=AsyncMock(return_value=99),
        send_fallback=AsyncMock(return_value=77),
        chunk_text=MagicMock(side_effect=lambda t: [t] if t else []),
        start_typing=MagicMock(),
        cancel_typing=MagicMock(),
        get_msg=MagicMock(side_effect=lambda key, fallback: fallback),
        placeholder_text="…",
    )


async def _async_iter(*evts: RenderEvent) -> AsyncIterator[RenderEvent]:
    for e in evts:
        yield e


class TestChunkText:
    def test_empty_string_returns_empty_list(self) -> None:
        assert chunk_text("", 10) == []

    def test_short_text_single_chunk(self) -> None:
        assert chunk_text("hello", 10) == ["hello"]

    def test_text_split_at_exact_boundary(self) -> None:
        assert chunk_text("abcdef", 3) == ["abc", "def"]

    def test_text_longer_than_max_len(self) -> None:
        result = chunk_text("a" * 10, 3)
        assert result == ["aaa", "aaa", "aaa", "a"]

    def test_max_len_of_one(self) -> None:
        assert chunk_text("abc", 1) == ["a", "b", "c"]

    def test_invalid_max_len_raises(self) -> None:
        with pytest.raises(ValueError, match="max_len"):
            chunk_text("x", 0)
        with pytest.raises(ValueError, match="max_len"):
            chunk_text("x", -1)

    def test_escape_fn_applied_before_chunking(self) -> None:
        # escape_fn doubles every character — max_len applies to post-escape length
        result = chunk_text("abc", 4, escape_fn=lambda t: "".join(c * 2 for c in t))
        # "aabbcc" split at 4 → ["aabb", "cc"]
        assert result == ["aabb", "cc"]

    def test_escape_fn_on_empty_returns_empty(self) -> None:
        assert chunk_text("", 10, escape_fn=str.upper) == []

    def test_no_escape_fn(self) -> None:
        # chunk_text splits at word boundaries, so "hello world" splits at the
        # space — each chunk is rstrip'd/lstrip'd, yielding two clean words.
        assert chunk_text("hello world", 5) == ["hello", "world"]


class TestIntermediateTextState:
    def test_fresh_instance_has_no_text(self) -> None:
        state = IntermediateTextState()
        assert state.text == ""
        assert state.display() == ""

    def test_append_first_segment_adds_hourglass_prefix(self) -> None:
        state = IntermediateTextState()
        state.append("hello")
        assert state.text == "⏳ hello"

    def test_append_second_segment_concats_raw(self) -> None:
        state = IntermediateTextState()
        state.append("hello")
        state.append(" world")
        assert state.text == "⏳ hello world"

    def test_append_empty_string_is_noop(self) -> None:
        state = IntermediateTextState()
        state.append("")
        assert state.text == ""
        assert state.display() == ""

    def test_append_multiple_segments_concat_raw(self) -> None:
        state = IntermediateTextState()
        state.append("a")
        state.append("b")
        state.append("c")
        assert state.text == "⏳ abc"

    def test_display_returns_empty_string_when_no_text(self) -> None:
        state = IntermediateTextState()
        assert state.display() == ""

    def test_display_returns_text_when_appended(self) -> None:
        state = IntermediateTextState()
        state.append("thinking")
        assert state.display() == "⏳ thinking"


class TestSendWithRetry:
    async def test_send_with_retry_succeeds_on_first_attempt(self) -> None:
        # Arrange
        coro_fn = AsyncMock(return_value=None)
        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await send_with_retry(coro_fn, label="test-op")
        # Assert
        coro_fn.assert_awaited_once()

    async def test_send_with_retry_retries_on_transient_failure(self) -> None:
        # Arrange — fails once, then succeeds
        coro_fn = AsyncMock(side_effect=[RuntimeError("transient"), None])
        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await send_with_retry(coro_fn, label="test-op")
        # Assert
        assert coro_fn.await_count == 2

    async def test_send_with_retry_gives_up_after_max_attempts(self) -> None:
        # Arrange — always raises
        coro_fn = AsyncMock(side_effect=RuntimeError("permanent"))
        # Act — must NOT re-raise; function returns normally after exhausting attempts
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await send_with_retry(coro_fn, label="test-op", max_attempts=3)
        # Assert
        assert coro_fn.await_count == 3

    async def test_send_with_retry_custom_max_attempts(self) -> None:
        # Arrange — always raises; custom limit of 2
        coro_fn = AsyncMock(side_effect=RuntimeError("permanent"))
        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await send_with_retry(coro_fn, label="test-op", max_attempts=2)
        # Assert — called at most max_attempts times
        assert coro_fn.await_count == 2


class TestFormatToolSummaryHeader:
    def test_format_tool_summary_header_complete(self) -> None:
        # Arrange
        event = ToolSummaryRenderEvent(is_complete=True)
        # Act
        result = format_tool_summary_header(event)
        # Assert
        assert result == "🔧 Done ✅"

    def test_format_tool_summary_header_in_progress(self) -> None:
        # Arrange
        event = ToolSummaryRenderEvent(is_complete=False)
        # Act
        result = format_tool_summary_header(event)
        # Assert
        assert result == "🔧 Working…"


class TestFormatUnknownTools:
    def test_single_unknown_tool_single_call(self) -> None:
        """One unknown tool called once renders as '🔧 1 todowrite'."""
        from lyra.core.messaging.tool_recap_format import format_tool_lines

        event = ToolSummaryRenderEvent(unknown_calls={"todowrite": 1})
        lines = format_tool_lines(event)
        assert lines == ["🔧 1 todowrite"]

    def test_multiple_unknown_tools_sorted(self) -> None:
        """Multiple unknown tools are sorted alphabetically, one line each."""
        from lyra.core.messaging.tool_recap_format import format_tool_lines

        event = ToolSummaryRenderEvent(unknown_calls={"todowrite": 3, "ls": 1})
        lines = format_tool_lines(event)
        assert lines == ["🔧 1 ls", "🔧 3 todowrite"]

    def test_unknown_tools_appear_before_silent_line(self) -> None:
        """Unknown tool lines come before the 🔍 silent counts line."""
        from lyra.core.messaging.render_events import SilentCounts
        from lyra.core.messaging.tool_recap_format import format_tool_lines

        event = ToolSummaryRenderEvent(
            unknown_calls={"todowrite": 2},
            silent_counts=SilentCounts(reads=5),
        )
        lines = format_tool_lines(event)
        unknown_idx = next(i for i, l in enumerate(lines) if "todowrite" in l)
        silent_idx = next(i for i, l in enumerate(lines) if "read" in l)
        assert unknown_idx < silent_idx

    def test_empty_unknown_calls_emits_no_lines(self) -> None:
        """No unknown tools → no extra lines in the recap."""
        from lyra.core.messaging.tool_recap_format import format_tool_lines

        event = ToolSummaryRenderEvent()
        lines = format_tool_lines(event)
        assert not any("🔧" in l for l in lines)


# ---------------------------------------------------------------------------
# T10 — v2 Text dispatch + fallback tests (#1099)
# ---------------------------------------------------------------------------


class TestDispatchTextStartToOnTextV2:
    """TextStartRenderEvent routes to _on_text_v2 and causes no v1 UX side-effects."""

    async def test_dispatch_routes_text_start_to_on_text_v2(self) -> None:
        # Arrange
        cb = _make_callbacks()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(cb, outbound=outbound)

        # Feed only a TextStartRenderEvent (no v1 TextRenderEvent follows).
        # The stream has no final text so _deliver_final will edit the placeholder
        # with an error message — but edit_placeholder_text must NOT have been
        # called during event dispatch (only during delivery).
        cb.edit_placeholder_text.reset_mock()  # type: ignore[attr-defined]

        await session._run_event_loop(
            _async_iter(TextStartRenderEvent(message_id="text-1")),
            placeholder_obj=object(),
        )

        # Assert — edit_placeholder_text must not have been called by the dispatch
        # loop itself (the no-op _on_text_v2 seam must absorb the event without
        # touching the placeholder). Negative: if the guard were removed and the
        # event fell through to assert_never, _run_event_loop would raise.
        cb.edit_placeholder_text.assert_not_called()  # type: ignore[attr-defined]


class TestDispatchAllFourV2TextTypes:
    """All 4 v2 Text types are routed through _on_text_v2, once each, in order."""

    async def test_dispatch_all_4_v2_text_types(self) -> None:
        # Arrange — subclass spy captures every call to _on_text_v2
        seen: list[type] = []

        class _SpySession(StreamingSession):
            async def _on_text_v2(
                self,
                event: TextStartRenderEvent
                | TextDeltaRenderEvent
                | TextEndRenderEvent
                | TextChunkRenderEvent,
            ) -> None:
                seen.append(type(event))

        cb = _make_callbacks()
        outbound = OutboundMessage.from_text("hi")
        session = _SpySession(cb, outbound=outbound)

        events = [
            TextStartRenderEvent(message_id="text-1"),
            TextDeltaRenderEvent(message_id="text-1", delta="x"),
            TextEndRenderEvent(message_id="text-1"),
            TextChunkRenderEvent(message_id="text-2", delta="y"),
        ]

        # Act
        await session._run_event_loop(
            _async_iter(*events),
            placeholder_obj=object(),
        )

        # Assert — spy called once per event type, in input order.
        # Negative: if _on_text_v2 dispatch branch is removed, seen stays [].
        assert seen == [
            TextStartRenderEvent,
            TextDeltaRenderEvent,
            TextEndRenderEvent,
            TextChunkRenderEvent,
        ]


class TestDrainFallbackHarvestsV2Delta:
    """_drain_fallback accumulates TextDeltaRenderEvent.delta when no v1 is present."""

    async def test_drain_fallback_harvests_v2_delta(self) -> None:
        # Arrange
        cb = _make_callbacks()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(cb, outbound=outbound)

        # Act — drain a v2-only stream
        await session._drain_fallback(
            _async_iter(TextDeltaRenderEvent(message_id="text-x", delta="Hello"))
        )

        # Assert — send_fallback received the delta text exactly.
        # Negative: if the isinstance(event, TextDeltaRenderEvent) branch is
        # removed, parts stays [] and send_fallback gets placeholder_text ("…"),
        # not "Hello".
        cb.send_fallback.assert_awaited_once_with("Hello")  # type: ignore[attr-defined]


class TestDrainFallbackDualEmitAccumulation:
    """Dual-emit accumulation in _drain_fallback.

    Under dual emission, v2 delta and v1 text are distinct event objects in the
    stream; each fires its own branch. The elif guards on type — they do NOT
    deduplicate same-content events. Slice 5 (#1102) removes the v1 branch.
    """

    async def test_drain_fallback_v2_only_yields_single_string(self) -> None:
        # A v2-only stream produces a single accumulation. Proves the v2 branch
        # consumes TextDeltaRenderEvent without also routing through the v1 branch.
        cb = _make_callbacks()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(cb, outbound=outbound)

        await session._drain_fallback(
            _async_iter(
                TextDeltaRenderEvent(message_id="text-1", delta="Hi"),
            )
        )

        cb.send_fallback.assert_awaited_once_with("Hi")  # type: ignore[attr-defined]

    async def test_drain_fallback_v1_and_v2_both_contribute_in_dual_emit(
        self,
    ) -> None:
        # Real dual-emit shape: v2 delta + v1 text in the same stream. Each fires
        # its own branch (types are disjoint). "HiHi" is the intended transient
        # behavior during coexistence; Slice 5 drops the v1 branch.
        cb = _make_callbacks()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(cb, outbound=outbound)

        await session._drain_fallback(
            _async_iter(
                TextDeltaRenderEvent(message_id="text-1", delta="Hi"),
                TextRenderEvent(text="Hi", is_final=True),
            )
        )

        cb.send_fallback.assert_awaited_once_with("HiHi")  # type: ignore[attr-defined]
