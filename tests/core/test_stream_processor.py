"""Tests for lyra.core.processors.stream_processor — StreamProcessor (S3)."""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import AsyncIterator

import pytest

from lyra.core.messaging.events import (
    ResultLlmEvent,
    TextLlmEvent,
    ThinkingLlmEvent,
    ToolResultLlmEvent,
    ToolUseDeltaLlmEvent,
    ToolUseEndLlmEvent,
    ToolUseLlmEvent,
)
from lyra.core.messaging.render_events import (
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
from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.core.processors.stream_processor import StreamProcessor
from lyra.core.trace import TraceContext

_RUN_LIFECYCLE_TYPES = (
    RunStartedRenderEvent,
    RunFinishedRenderEvent,
    RunErrorRenderEvent,
)

# Slice 3 (#1100) v2 events. Stripped from v1-focused test results so the
# existing assertions remain stable; ToolCall*-specific tests live in
# TestToolCallLifecycle and inspect the unstripped stream.
_TOOLCALL_V2_TYPES = (
    ToolCallStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
)

# Slice 2 (#1099) v2 Text triplet events. Stripped from v1-focused test results
# so their original v1 TextRenderEvent / ToolSummaryRenderEvent assertions remain
# stable. v2 triplet ordering is asserted in TestTextTriplet.
_TEXT_V2_TYPES = (
    TextStartRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextChunkRenderEvent,
)


def strip_run_lifecycle(events: list[RenderEvent]) -> list[RenderEvent]:
    """Drop Run*, ToolCall*, and Text{Start,Delta,End} events.

    Slice 1 added Run lifecycle bookends; Slice 3 added per-call ToolCall*
    streaming; Slice 2 added v2 Text triplet events. v1-focused tests strip
    all three so their original (v1 ``TextRenderEvent`` +
    ``ToolSummaryRenderEvent``) assertions remain stable. v2 behaviour is
    asserted in dedicated test classes (``TestRunLifecycle``,
    ``TestToolCallLifecycle``, ``TestTextTriplet``).
    """
    _strip = (*_RUN_LIFECYCLE_TYPES, *_TOOLCALL_V2_TYPES, *_TEXT_V2_TYPES)
    return [e for e in events if not isinstance(e, _strip)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def collect(agen) -> list:
    """Drain an async generator into a list."""
    return [item async for item in agen]


async def async_events(*evts) -> AsyncIterator:
    """Yield events as an async iterator."""
    for e in evts:
        yield e


def cfg(**kw: object) -> ToolDisplayConfig:
    """Base config: throttle_ms=0 (disabled). Override via kw."""
    defaults: dict[str, object] = dict(
        names_threshold=3, group_threshold=3, bash_max_len=60, throttle_ms=0
    )
    return ToolDisplayConfig.model_validate({**defaults, **kw})


# ---------------------------------------------------------------------------
# T9 — T24: StreamProcessor integration tests
# ---------------------------------------------------------------------------


class TestStreamProcessor:
    """StreamProcessor integration tests (SC-1 through SC-10)."""

    # ------------------------------------------------------------------
    # T9 — Text-only turn (SC-3) — v2 triplet (Slice 3 / #1192)
    # ------------------------------------------------------------------

    async def test_text_only(self) -> None:
        """Text-only turn: v2 triplet TextStart → TextDelta × 2 → TextEnd.

        After Slice 3 (v1 removal), strip_run_lifecycle is not needed — the
        stream no longer emits TextRenderEvent(is_final=False) intermediates.
        The full unstripped stream (minus Run lifecycle bookends) contains:
          TextStartRenderEvent, TextDeltaRenderEvent × 2, TextEndRenderEvent.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="Hello "),
            TextLlmEvent(text="world"),
            ResultLlmEvent(is_error=False, duration_ms=100),
        )

        # Act — collect full stream; filter only Run lifecycle bookends
        all_events = await collect(processor.process(events))
        result = [e for e in all_events if not isinstance(e, _RUN_LIFECYCLE_TYPES)]

        # Assert — v2 triplet: Start → Delta × 2 → End (no v1 TextRenderEvent)
        assert len(result) == 4, f"Expected 4 events, got {len(result)}: {result!r}"
        start, delta1, delta2, end = result
        assert isinstance(start, TextStartRenderEvent)
        assert isinstance(delta1, TextDeltaRenderEvent)
        assert delta1.delta == "Hello "
        assert delta1.message_id == start.message_id
        assert isinstance(delta2, TextDeltaRenderEvent)
        assert delta2.delta == "world"
        assert delta2.message_id == start.message_id
        assert isinstance(end, TextEndRenderEvent)
        assert end.message_id == start.message_id

    # ------------------------------------------------------------------
    # T10 — Single Edit tool call (SC-1, SC-2) — v2 triplet (Slice 3 / #1192)
    # ------------------------------------------------------------------

    async def test_single_edit(self) -> None:
        """Single Edit: v2 text triplet + ToolCall{Start,End} (B8-1, #1211 S4).

        v2 contract (post-v1 removal): ToolSummaryRenderEvent is gone.
        Text before the ToolUse opens a text block (TextStart → TextDelta);
        ToolUseLlmEvent closes the block (TextEnd) then emits ToolCallStart.
        ResultLlmEvent synthesises a ToolCallEnd for the open call.
        Stream (minus Run lifecycle): TextStart → TextDelta → TextEnd →
          ToolCallStart → ToolCallEnd.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="Refactoring..."),
            ToolUseLlmEvent(
                tool_name="Edit", tool_id="t1", input={"path": "src/foo.py"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act — strip only Run lifecycle bookends
        all_events = await collect(processor.process(events))
        result = [e for e in all_events if not isinstance(e, _RUN_LIFECYCLE_TYPES)]

        # Assert — v2 text triplet present and correlated
        starts = [e for e in result if isinstance(e, TextStartRenderEvent)]
        deltas = [e for e in result if isinstance(e, TextDeltaRenderEvent)]
        ends = [e for e in result if isinstance(e, TextEndRenderEvent)]
        assert len(starts) == 1, f"Expected 1 TextStart, got {len(starts)}"
        assert len(deltas) == 1, f"Expected 1 TextDelta, got {len(deltas)}"
        assert len(ends) == 1, f"Expected 1 TextEnd, got {len(ends)}"
        assert deltas[0].message_id == starts[0].message_id
        assert ends[0].message_id == starts[0].message_id
        assert deltas[0].delta == "Refactoring..."

        # Assert — ToolCall lifecycle present
        tool_starts = [e for e in result if isinstance(e, ToolCallStartRenderEvent)]
        tool_ends = [e for e in result if isinstance(e, ToolCallEndRenderEvent)]
        assert len(tool_starts) == 1, (
            f"Expected 1 ToolCallStart, got {len(tool_starts)}"
        )
        assert len(tool_ends) == 1, f"Expected 1 ToolCallEnd, got {len(tool_ends)}"
        assert tool_starts[0].tool_call_id == "t1"
        assert tool_starts[0].tool_name == "Edit"
        assert tool_ends[0].tool_call_id == "t1"

        # Assert — file accumulator updated
        assert "src/foo.py" in processor._files

    async def test_single_edit_no_intermediate(self) -> None:
        """Single Edit: no intermediate ToolSummaryRenderEvent in v2 (L01).

        v2 contract (post-v1 removal): there is no mid-turn ToolSummaryRenderEvent
        at all. StreamProcessor emits the v2 Text triplet (TextStart → TextDelta →
        TextEnd) and ToolCallStart/End lifecycle events. The text block closes when
        the ToolUseLlmEvent arrives, and ToolCallEnd is synthesised at ResultLlmEvent.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="Refactoring..."),
            ToolUseLlmEvent(
                tool_name="Edit", tool_id="t1", input={"path": "src/foo.py"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act — strip only Run lifecycle bookends
        all_events = await collect(processor.process(events))
        result = [e for e in all_events if not isinstance(e, _RUN_LIFECYCLE_TYPES)]

        # Assert — v2 Text triplet present and correlated
        starts = [e for e in result if isinstance(e, TextStartRenderEvent)]
        deltas = [e for e in result if isinstance(e, TextDeltaRenderEvent)]
        ends = [e for e in result if isinstance(e, TextEndRenderEvent)]
        assert len(starts) == 1
        assert len(deltas) == 1
        assert len(ends) == 1
        assert deltas[0].delta == "Refactoring..."
        assert starts[0].message_id == deltas[0].message_id == ends[0].message_id

        # Assert — ToolCall lifecycle present (no mid-turn intermediate summary)
        tool_starts = [e for e in result if isinstance(e, ToolCallStartRenderEvent)]
        tool_ends = [e for e in result if isinstance(e, ToolCallEndRenderEvent)]
        assert len(tool_starts) == 1
        assert tool_starts[0].tool_call_id == "t1"
        assert tool_starts[0].tool_name == "Edit"
        assert len(tool_ends) == 1
        assert tool_ends[0].tool_call_id == "t1"

        # Assert — text block closes BEFORE ToolCallStart (TextEnd then ToolStart order)
        text_end_idx = next(
            i for i, e in enumerate(result) if isinstance(e, TextEndRenderEvent)
        )
        tool_start_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ToolCallStartRenderEvent)
        )
        assert text_end_idx < tool_start_idx

    async def test_write_tool_tracked(self) -> None:
        """Write tool calls emit ToolCallStart/End and update _files (B8-2, #1211 S4).

        v2 contract: Write tool emits the same ToolCall lifecycle as Edit.
        The file accumulator records the path. No ToolSummaryRenderEvent exists
        post-v1 removal; internal state (_files) is the authoritative record.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Write", tool_id="w1", input={"path": "src/new.py"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — ToolCall lifecycle emitted for Write
        tool_starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        tool_ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(tool_starts) == 1
        assert tool_starts[0].tool_call_id == "w1"
        assert tool_starts[0].tool_name == "Write"
        assert len(tool_ends) == 1
        assert tool_ends[0].tool_call_id == "w1"

        # Assert — file accumulator updated with Write path
        assert "src/new.py" in processor._files
        entry = processor._files["src/new.py"]
        assert entry.count == 1
        assert "Write" in entry.edits

    # ------------------------------------------------------------------
    # T11 — Five edits at threshold (SC-4: names mode)
    # ------------------------------------------------------------------

    async def test_five_edits_at_threshold(self) -> None:
        """Exactly names_threshold edits: names mode preserved (B8-3, #1211 S4).

        v2 contract: 5 Edit calls at names_threshold=5 keeps names mode —
        the edits list has 5 entries (not cleared to count-only). Each Edit
        emits one ToolCallStart; orphan synthesis at ResultLlmEvent emits 5
        ToolCallEnd events. Internal _files accumulator carries the edits list.
        """
        # Arrange
        processor = StreamProcessor(cfg(names_threshold=5))
        edit_events = [
            ToolUseLlmEvent(
                tool_name="Edit", tool_id=f"t{i}", input={"path": "src/foo.py"}
            )
            for i in range(5)
        ]
        events = async_events(
            *edit_events, ResultLlmEvent(is_error=False, duration_ms=50)
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — 5 ToolCallStart + 5 ToolCallEnd emitted
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 5, f"Expected 5 ToolCallStart, got {len(starts)}"
        assert len(ends) == 5, f"Expected 5 ToolCallEnd, got {len(ends)}"
        assert [e.tool_call_id for e in starts] == [f"t{i}" for i in range(5)]

        # Assert — file accumulator in names mode (count=5, edits list populated)
        assert "src/foo.py" in processor._files
        entry = processor._files["src/foo.py"]
        assert entry.count == 5
        assert len(entry.edits) == 5  # names mode — at threshold, not cleared

    # ------------------------------------------------------------------
    # T12 — Six edits: count mode (SC-4: threshold+1)
    # ------------------------------------------------------------------

    async def test_six_edits_count_mode(self) -> None:
        """names_threshold+1 edits switches to count mode (B8-4, #1211 S4).

        v2 contract: 6 Edit calls at names_threshold=5 triggers count mode —
        the edits list is cleared to [] and only count is tracked. Each Edit
        still emits ToolCallStart; orphan synthesis emits 6 ToolCallEnd events.
        """
        # Arrange
        processor = StreamProcessor(cfg(names_threshold=5))
        edit_events = [
            ToolUseLlmEvent(
                tool_name="Edit", tool_id=f"t{i}", input={"path": "src/foo.py"}
            )
            for i in range(6)
        ]
        events = async_events(
            *edit_events, ResultLlmEvent(is_error=False, duration_ms=50)
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — 6 ToolCallStart + 6 ToolCallEnd emitted
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 6, f"Expected 6 ToolCallStart, got {len(starts)}"
        assert len(ends) == 6, f"Expected 6 ToolCallEnd, got {len(ends)}"

        # Assert — file accumulator in count mode (edits cleared)
        assert "src/foo.py" in processor._files
        entry = processor._files["src/foo.py"]
        assert entry.count == 6
        assert entry.edits == []  # count mode: cleared when count > names_threshold

    # ------------------------------------------------------------------
    # T13 — Two files, no group (SC-5)
    # ------------------------------------------------------------------

    async def test_two_files_no_group(self) -> None:
        """Two distinct files below group_threshold: each tracked in _files (L02).

        v2 contract: 2 Edit calls at group_threshold=3 stays per-file — both
        paths are tracked in the _files accumulator. Each Edit emits one
        ToolCallStart; orphan synthesis at ResultLlmEvent emits 2 ToolCallEnd.
        No v1 ToolSummaryRenderEvent; _files is the authoritative record.
        """
        # Arrange
        processor = StreamProcessor(cfg(group_threshold=3))
        events = async_events(
            ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={"path": "a.py"}),
            ToolUseLlmEvent(tool_name="Edit", tool_id="t2", input={"path": "b.py"}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — 2 ToolCallStart + 2 ToolCallEnd emitted
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 2, f"Expected 2 ToolCallStart, got {len(starts)}"
        assert len(ends) == 2, f"Expected 2 ToolCallEnd, got {len(ends)}"
        assert {e.tool_call_id for e in starts} == {"t1", "t2"}

        # Assert — both file paths tracked per-file in _files accumulator
        assert "a.py" in processor._files
        assert "b.py" in processor._files
        assert processor._files["a.py"].count == 1
        assert processor._files["b.py"].count == 1

    # ------------------------------------------------------------------
    # T14 — Three files at group_threshold (SC-5)
    # ------------------------------------------------------------------

    async def test_three_files_group(self) -> None:
        """Three distinct files at group_threshold: all tracked in _files (L03).

        v2 contract: 3 Edit calls at group_threshold=3 — all three are tracked
        in the _files accumulator. Each Edit emits one ToolCallStart; orphan
        synthesis at ResultLlmEvent emits 3 ToolCallEnd events. No v1
        ToolSummaryRenderEvent; _files is the authoritative record. Group-display
        decisions live at the adapter layer, not StreamProcessor.
        """
        # Arrange
        processor = StreamProcessor(cfg(group_threshold=3))
        events = async_events(
            ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={"path": "a.py"}),
            ToolUseLlmEvent(tool_name="Edit", tool_id="t2", input={"path": "b.py"}),
            ToolUseLlmEvent(tool_name="Edit", tool_id="t3", input={"path": "c.py"}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — 3 ToolCallStart + 3 ToolCallEnd emitted
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 3, f"Expected 3 ToolCallStart, got {len(starts)}"
        assert len(ends) == 3, f"Expected 3 ToolCallEnd, got {len(ends)}"
        assert {e.tool_call_id for e in starts} == {"t1", "t2", "t3"}

        # Assert — all three file paths tracked in _files accumulator
        assert "a.py" in processor._files
        assert "b.py" in processor._files
        assert "c.py" in processor._files
        assert processor._files["a.py"].count == 1
        assert processor._files["b.py"].count == 1
        assert processor._files["c.py"].count == 1

    # ------------------------------------------------------------------
    # T15 — 80 edits over 5 files (SC-4, SC-5)
    # ------------------------------------------------------------------

    async def test_eighty_tools_multi_file(self) -> None:
        """80 edits cycling 5 files: each file gets count==16 in count mode (L04).

        v2 contract: 80 Edit calls cycling 5 files at names_threshold=3 puts all
        files into count mode (16 > 3). Each Edit emits ToolCallStart; orphan
        synthesis at ResultLlmEvent emits 80 ToolCallEnd events. _files accumulator
        holds 5 entries each with count==16 and edits==[].
        """
        # Arrange
        processor = StreamProcessor(cfg(names_threshold=3))
        file_names = [f"src/file{i}.py" for i in range(5)]
        edit_events = [
            ToolUseLlmEvent(
                tool_name="Edit", tool_id=f"t{i}", input={"path": file_names[i % 5]}
            )
            for i in range(80)
        ]
        events = async_events(
            *edit_events, ResultLlmEvent(is_error=False, duration_ms=50)
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — 80 ToolCallStart + 80 ToolCallEnd emitted
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 80, f"Expected 80 ToolCallStart, got {len(starts)}"
        assert len(ends) == 80, f"Expected 80 ToolCallEnd, got {len(ends)}"

        # Assert — _files accumulator has 5 entries all in count mode
        assert len(processor._files) == 5
        for file_name in file_names:
            assert file_name in processor._files
            entry = processor._files[file_name]
            assert entry.count == 16, (
                f"{file_name}: expected count=16, got {entry.count}"
            )
            assert entry.edits == [], (
                f"{file_name}: expected count mode (edits=[]), got {entry.edits}"
            )

    # ------------------------------------------------------------------
    # T16 — Bash truncation (SC-6)
    # ------------------------------------------------------------------

    async def test_bash_truncation(self) -> None:
        """Bash commands > bash_max_len truncated in _bash accumulator (B8-5, #1211 S4).

        v2 contract: Bash tool emits ToolCallStart/End like any other tool.
        The command is stored in _bash accumulator truncated to bash_max_len.
        No ToolSummaryRenderEvent post-v1 removal; accumulator is authoritative.
        """
        # Arrange
        processor = StreamProcessor(cfg(bash_max_len=60))
        long_command = "x" * 80
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Bash", tool_id="b1", input={"command": long_command}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — ToolCall lifecycle emitted for Bash
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 1
        assert starts[0].tool_name == "Bash"
        assert len(ends) == 1

        # Assert — bash accumulator has truncated command
        assert len(processor._bash) == 1
        assert len(processor._bash[0]) == 60  # truncated to bash_max_len

    # ------------------------------------------------------------------
    # T17 — Silent Read/Grep/Glob (SC-7)
    # ------------------------------------------------------------------

    async def test_silent_read_grep_glob(self) -> None:
        """Read/Grep/Glob: ToolCall lifecycle + silent counters (B8-6, #1211 S4).

        v2 contract: ToolCallStart is emitted for every tool (including silent ones)
        since _handle_tool_event always yields it. Silent Read/Grep/Glob update
        the _silent_reads/_silent_greps/_silent_globs counters but do NOT add to
        _files, _bash, _web_fetches, or _agent_calls.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(tool_name="Read", tool_id="r1", input={}),
            ToolUseLlmEvent(tool_name="Grep", tool_id="g1", input={}),
            ToolUseLlmEvent(tool_name="Glob", tool_id="gl1", input={}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — 3 ToolCallStart + 3 ToolCallEnd (orphan synthesis) emitted
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 3, f"Expected 3 ToolCallStart, got {len(starts)}"
        assert len(ends) == 3, f"Expected 3 ToolCallEnd, got {len(ends)}"
        assert {e.tool_name for e in starts} == {"Read", "Grep", "Glob"}

        # Assert — silent counters incremented
        assert processor._silent_reads == 1
        assert processor._silent_greps == 1
        assert processor._silent_globs == 1

        # Assert — no file/bash/web accumulator entries
        assert processor._files == {}
        assert processor._bash == []
        assert processor._web_fetches == []
        assert processor._agent_calls == []

    # ------------------------------------------------------------------
    # T18 — WebFetch visible (SC-9)
    # ------------------------------------------------------------------

    async def test_web_fetch_visible(self) -> None:
        """WebFetch: ToolCallStart/End + URL in _web_fetches (B8-7, #1211 S4).

        v2 contract: WebFetch (show["web_fetch"]=True by default) emits the
        ToolCall lifecycle and appends the URL to _web_fetches. No
        ToolSummaryRenderEvent post-v1 removal.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(
                tool_name="WebFetch",
                tool_id="wf1",
                input={"url": "https://example.com"},
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — ToolCall lifecycle emitted
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 1
        assert starts[0].tool_name == "WebFetch"
        assert starts[0].tool_call_id == "wf1"
        assert len(ends) == 1

        # Assert — URL accumulated (show["web_fetch"]=True by default)
        assert len(processor._web_fetches) == 1
        assert processor._web_fetches[0] == "https://example.com"

    async def test_web_search_visible(self) -> None:
        """WebSearch: ToolCallStart/End emitted + query in _web_fetches (L05).

        v2 contract: WebSearch (show["web_search"]=True by default) emits the
        ToolCall lifecycle and appends the query to _web_fetches accumulator.
        Parity with the active test_web_fetch_visible (line 526).
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(
                tool_name="WebSearch",
                tool_id="ws1",
                input={"query": "python asyncio"},
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — ToolCall lifecycle emitted
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 1
        assert starts[0].tool_name == "WebSearch"
        assert starts[0].tool_call_id == "ws1"
        assert len(ends) == 1
        assert ends[0].tool_call_id == "ws1"

        # Assert — query accumulated in _web_fetches (show["web_search"]=True)
        assert len(processor._web_fetches) == 1
        assert processor._web_fetches[0] == "python asyncio"

    async def test_web_fetch_hidden_when_show_false(self) -> None:
        """WebFetch show=False: lifecycle emitted, URL not accumulated (L06).

        v2 contract: show["web_fetch"]=False suppresses URL accumulation in
        _web_fetches but does NOT suppress ToolCallStart/End lifecycle events
        (those are always emitted by _handle_tool_event). The show flag only
        controls the _accumulate_web call.
        """
        # Arrange
        config = ToolDisplayConfig.model_validate({"show": {"web_fetch": False}})
        processor = StreamProcessor(config)
        events = async_events(
            ToolUseLlmEvent(
                tool_name="WebFetch",
                tool_id="wf1",
                input={"url": "https://example.com"},
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — ToolCallStart/End still emitted (lifecycle always fires)
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 1
        assert starts[0].tool_name == "WebFetch"
        assert len(ends) == 1

        # Assert — URL NOT accumulated (show["web_fetch"]=False)
        assert processor._web_fetches == [], (
            f"Expected empty _web_fetches when show=False, got {processor._web_fetches}"
        )

    # ------------------------------------------------------------------
    # T19 — Agent calls accumulation (SC-10)
    # ------------------------------------------------------------------

    async def test_agent_calls_accumulation(self) -> None:
        """Agent calls: ToolCallStart/End + description in _agent_calls (L07).

        v2 contract: the live _agent_calls path in StreamProcessor._accumulate
        appends event.input["description"] when show["agent"]=True (default).
        ToolCall lifecycle is always emitted. _agent_calls is authoritative.
        Targets stream_processor.py:157,532,542.

        Two calls used to verify list accumulation (single item would not
        distinguish append from replace).
        """
        # Arrange — default cfg() has show["agent"]=True
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Agent", tool_id="a1", input={"description": "sub-task"}
            ),
            ToolUseLlmEvent(
                tool_name="Agent", tool_id="a2", input={"description": "another-task"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — ToolCall lifecycle emitted for both agent calls
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 2
        assert {e.tool_call_id for e in starts} == {"a1", "a2"}
        assert len(ends) == 2

        # Assert — agent call descriptions accumulated in _agent_calls (live path)
        assert processor._agent_calls == ["sub-task", "another-task"]

        # Assert — _has_any_tool_events() returns True when _agent_calls is non-empty
        assert processor._has_any_tool_events()

    async def test_agent_calls_skipped_when_show_agent_false(self) -> None:
        """L07 negative: show={"agent": False} → `_agent_calls` stays empty."""
        # Arrange
        config = ToolDisplayConfig.model_validate(
            {
                "show": {"agent": False},
                "throttle_ms": 0,
            }
        )
        processor = StreamProcessor(config)
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Agent", tool_id="a1", input={"description": "sub-task-1"}
            ),
            ToolUseEndLlmEvent(tool_id="a1"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        await collect(processor.process(events))

        # Assert — show=False means _agent_calls stays empty (no accumulation)
        assert processor._agent_calls == []

    # ------------------------------------------------------------------
    # T20 — ResultLlmEvent bypasses throttle (SC-8)
    # ------------------------------------------------------------------

    async def test_result_bypasses_throttle(self) -> None:
        """ToolCallEnd is always emitted at ResultLlmEvent time (B8-8, #1211 S4).

        v2 contract: throttle_ms is stored in ToolDisplayConfig but not used for
        suppression in StreamProcessor (v1 ToolSummaryRenderEvent throttle is gone).
        Every tool call gets a ToolCallStart immediately, and a ToolCallEnd either
        via ToolUseEndLlmEvent or orphan synthesis at ResultLlmEvent. Setting a
        large throttle_ms has no effect on v2 ToolCall* emission.
        """
        # Arrange
        processor = StreamProcessor(cfg(throttle_ms=9_999_999))
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Edit", tool_id="t1", input={"path": "src/foo.py"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — ToolCallStart and ToolCallEnd both emitted (throttle has no effect)
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 1, f"Expected 1 ToolCallStart, got {len(starts)}"
        assert len(ends) == 1, f"Expected 1 ToolCallEnd, got {len(ends)}"
        assert starts[0].tool_call_id == "t1"
        assert ends[0].tool_call_id == "t1"

        # Assert — run lifecycle closes cleanly
        assert any(isinstance(e, RunFinishedRenderEvent) for e in all_events)

    # ------------------------------------------------------------------
    # T21 — Throttle suppresses duplicate mid-turn events (SC-8)
    # ------------------------------------------------------------------

    async def test_throttle_suppression(self) -> None:
        """Multiple tools each get their own ToolCallStart/End (B8-9, #1211 S4).

        v2 contract: there is no mid-turn ToolSummaryRenderEvent throttle
        post-v1 removal. Two Edit tool calls produce 2 ToolCallStart + 2
        ToolCallEnd events regardless of throttle_ms setting. Both file paths
        appear in the _files accumulator.
        """
        # Arrange
        processor = StreamProcessor(cfg(throttle_ms=9_999_999))
        events = async_events(
            ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={"path": "a.py"}),
            ToolUseLlmEvent(tool_name="Edit", tool_id="t2", input={"path": "b.py"}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — 2 ToolCallStart + 2 ToolCallEnd (no v2 throttle suppression)
        starts = [e for e in all_events if isinstance(e, ToolCallStartRenderEvent)]
        ends = [e for e in all_events if isinstance(e, ToolCallEndRenderEvent)]
        assert len(starts) == 2, f"Expected 2 ToolCallStart, got {len(starts)}"
        assert len(ends) == 2, f"Expected 2 ToolCallEnd, got {len(ends)}"
        assert {e.tool_call_id for e in starts} == {"t1", "t2"}

        # Assert — both file paths in accumulator
        assert "a.py" in processor._files
        assert "b.py" in processor._files

    # ------------------------------------------------------------------
    # T23 — Text accumulation across multiple chunks (SC-2)
    # ------------------------------------------------------------------

    async def test_text_accumulation(self) -> None:
        """TextDelta.delta values concatenated reproduce the input text (L09).

        v2 contract: each TextLlmEvent chunk becomes one TextDeltaRenderEvent.
        Concatenating all TextDeltaRenderEvent.delta values in order must
        reproduce the original input text. Distinct from test_text_triplet_*
        (which assert ordering and message_id correlation) — this test asserts
        the delta-concatenation property within a single text block.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="Hello"),
            TextLlmEvent(text=" "),
            TextLlmEvent(text="world"),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — exactly 3 TextDelta events (one per TextLlmEvent)
        deltas = [e for e in result if isinstance(e, TextDeltaRenderEvent)]
        assert len(deltas) == 3, f"Expected 3 TextDelta, got {len(deltas)}"
        assert [d.delta for d in deltas] == ["Hello", " ", "world"]

        # Assert — concatenating all deltas reproduces the original input
        reconstructed = "".join(d.delta for d in deltas)
        assert reconstructed == "Hello world"

        # Assert — all deltas share the same message_id (single block)
        mid = deltas[0].message_id
        assert all(d.message_id == mid for d in deltas)

    # ------------------------------------------------------------------
    # B3 — is_error propagation from ResultLlmEvent → TextRenderEvent (#392)
    # ------------------------------------------------------------------

    async def test_is_error_propagated_to_text_render_event(self) -> None:
        """ResultLlmEvent(is_error=True) → RunErrorRenderEvent (v2, #1211 S3).

        v2 contract: is_error on the Result closes the open text block via
        TextEndRenderEvent, then emits RunErrorRenderEvent (not RunFinishedRenderEvent).
        The error flag is carried on the run-level event, not on TextEnd.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="error response"),
            ResultLlmEvent(is_error=True, duration_ms=0),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — run envelope uses RunError (not RunFinished) for is_error=True
        assert isinstance(all_events[0], RunStartedRenderEvent)
        assert isinstance(all_events[-1], RunErrorRenderEvent)
        assert not any(isinstance(e, RunFinishedRenderEvent) for e in all_events)

        # Assert — text block is properly closed before the run terminates
        text_starts = [e for e in all_events if isinstance(e, TextStartRenderEvent)]
        text_deltas = [e for e in all_events if isinstance(e, TextDeltaRenderEvent)]
        text_ends = [e for e in all_events if isinstance(e, TextEndRenderEvent)]
        assert len(text_starts) == 1
        assert len(text_deltas) == 1
        assert text_deltas[0].delta == "error response"
        assert text_deltas[0].message_id == text_starts[0].message_id
        assert len(text_ends) == 1
        assert text_ends[0].message_id == text_starts[0].message_id

    async def test_is_error_run_error_carries_error_text(self) -> None:
        """ResultLlmEvent(is_error=True, error_text=...) → RunErrorRenderEvent.message.

        Confirms the v2 contract: soft-error text is carried on the run-level
        terminal event (RunErrorRenderEvent.message), not buried in a text event.
        """
        processor = StreamProcessor(cfg())
        events = async_events(
            ResultLlmEvent(is_error=True, duration_ms=0, error_text="boom"),
        )

        all_events = await collect(processor.process(events))

        run_errors = [e for e in all_events if isinstance(e, RunErrorRenderEvent)]
        assert len(run_errors) == 1
        assert run_errors[0].message == "boom"

    async def test_is_error_false_propagated_to_text_render_event(self) -> None:
        """ResultLlmEvent(is_error=False) → RunFinishedRenderEvent, not RunError (L10).

        v2 contract: is_error=False closes the text block cleanly via
        TextEndRenderEvent, then emits RunFinishedRenderEvent (outcome=success).
        No RunErrorRenderEvent is emitted. Parity-False case for the active
        test_is_error_propagated_to_text_render_event (is_error=True).
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="normal response"),
            ResultLlmEvent(is_error=False, duration_ms=0),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — terminal is RunFinished (not RunError) for is_error=False
        assert isinstance(all_events[-1], RunFinishedRenderEvent)
        assert all_events[-1].outcome == "success"
        assert not any(isinstance(e, RunErrorRenderEvent) for e in all_events)

        # Assert — text block properly closed
        text_ends = [e for e in all_events if isinstance(e, TextEndRenderEvent)]
        text_deltas = [e for e in all_events if isinstance(e, TextDeltaRenderEvent)]
        assert len(text_ends) == 1
        assert len(text_deltas) == 1
        assert text_deltas[0].delta == "normal response"
        text_starts = [e for e in all_events if isinstance(e, TextStartRenderEvent)]
        assert len(text_starts) == 1
        assert text_ends[0].message_id == text_starts[0].message_id

    async def test_error_text_surfaces_when_no_streamed_text(self) -> None:
        """L11: error_text surfaces via RunError.message when no streamed text exists.

        Extends `test_is_error_run_error_carries_error_text` with the additional
        guarantee that no text block is opened (no TextStart/Delta) when
        `error_text` is the only text-bearing field.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ResultLlmEvent(
                is_error=True,
                duration_ms=15,
                error_text="Not logged in · Please run /login",
            ),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — terminal RunError with error_text as message
        run_errors = [e for e in all_events if isinstance(e, RunErrorRenderEvent)]
        assert len(run_errors) == 1
        assert run_errors[0].message == "Not logged in · Please run /login"

        # Assert — no text block opened (no streamed text)
        assert not any(isinstance(e, TextStartRenderEvent) for e in all_events)
        assert not any(isinstance(e, TextDeltaRenderEvent) for e in all_events)

    async def test_streamed_text_preferred_over_error_text(self) -> None:
        """Streamed text + error_text: text in TextDelta, error_text in RunError (L12).

        v2 contract: when both streamed text and error_text are present,
        the streamed text appears in TextDeltaRenderEvent.delta (as it arrived),
        and error_text is forwarded as RunErrorRenderEvent.message. The error
        does not suppress or replace the streamed text.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="recovered output"),
            ResultLlmEvent(
                is_error=True,
                duration_ms=100,
                error_text="should be in RunError",
            ),
        )

        # Act
        all_events = await collect(processor.process(events))

        # Assert — streamed text appears in TextDelta (unchanged)
        text_deltas = [e for e in all_events if isinstance(e, TextDeltaRenderEvent)]
        assert len(text_deltas) == 1
        assert text_deltas[0].delta == "recovered output"

        # Assert — error_text forwarded as RunError.message
        run_errors = [e for e in all_events if isinstance(e, RunErrorRenderEvent)]
        assert len(run_errors) == 1
        assert run_errors[0].message == "should be in RunError"

    async def test_empty_stream(self) -> None:
        """Empty event stream emits the v2 minimum envelope (v2, #1211 S3).

        v2 contract: no LlmEvents → no TextBlock, no RunError. The processor
        receives nothing (ResultLlmEvent never arrives), so _result_is_error
        stays False and the run closes cleanly with RunFinishedRenderEvent.
        Minimum envelope: RunStarted → RunFinished only.
        """
        # Arrange
        processor = StreamProcessor(cfg())

        # Act
        result = await collect(processor.process(async_events()))

        # Assert — exactly two lifecycle bookends, no text or error events
        assert len(result) == 2, f"Expected 2 events, got {len(result)}: {result!r}"
        assert isinstance(result[0], RunStartedRenderEvent)
        assert isinstance(result[1], RunFinishedRenderEvent)
        assert result[1].outcome == "success"
        assert not any(isinstance(e, RunErrorRenderEvent) for e in result)
        _text_types = (TextStartRenderEvent, TextDeltaRenderEvent, TextEndRenderEvent)
        assert not any(isinstance(e, _text_types) for e in result)

    async def test_no_result_event(self) -> None:
        """Truncated stream without ResultLlmEvent: TextEnd emitted, RunFinished (L13).

        v2 contract: when the stream ends without a ResultLlmEvent, the
        truncation path (stream_processor.py:341–356) closes any open text
        block via TextEndRenderEvent, then the post-finally path emits
        RunFinishedRenderEvent (outcome=success) because _result_is_error stays
        False. No v1 TextRenderEvent(is_final=False) fallback.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(TextLlmEvent(text="partial response"))

        # Act
        result = await collect(processor.process(events))

        # Assert — text block opened and properly closed (truncation guard fires)
        text_starts = [e for e in result if isinstance(e, TextStartRenderEvent)]
        text_deltas = [e for e in result if isinstance(e, TextDeltaRenderEvent)]
        text_ends = [e for e in result if isinstance(e, TextEndRenderEvent)]
        assert len(text_starts) == 1
        assert len(text_deltas) == 1
        assert text_deltas[0].delta == "partial response"
        assert len(text_ends) == 1
        assert text_ends[0].message_id == text_starts[0].message_id

        # Assert — terminal is RunFinished (no exception, no error result)
        assert isinstance(result[-1], RunFinishedRenderEvent)
        assert not any(isinstance(e, RunErrorRenderEvent) for e in result)

    # ------------------------------------------------------------------
    # T24 — Hexagonal boundary (no framework imports in stream_processor)
    # ------------------------------------------------------------------

    def test_hexagonal_boundary(self) -> None:
        """stream_processor.py must not import aiogram, discord, or anthropic."""
        # Arrange
        _root = Path(__file__).resolve().parent.parent.parent
        source_path = (
            _root / "src" / "lyra" / "core" / "processors" / "stream_processor.py"
        )

        if not source_path.exists():
            import pytest

            pytest.fail("source not found: stream_processor.py moved or deleted")

        forbidden = {"aiogram", "discord", "anthropic"}

        # Act
        tree = ast.parse(source_path.read_text())

        # Assert
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [alias.name for alias in node.names]
                )
                for name in names:
                    for f in forbidden:
                        assert not (name or "").startswith(f), (
                            f"{source_path}: forbidden import '{name}'"
                        )


# ---------------------------------------------------------------------------
# Slice 1 of #1096 — Run lifecycle events (#1098)
# ---------------------------------------------------------------------------


class TestRunLifecycle:
    """RunStarted/RunFinished/RunError emission contract (#1098)."""

    async def test_emission_order_text_only(self) -> None:
        """Text-only turn: exact v2 ordered event sequence (B8-12, #1211 S4).

        v2 contract: RunStarted → TextStart → TextDelta → TextEnd → RunFinished.
        No v1 TextRenderEvent in the stream post-v1 removal.
        """
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="hello"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))

        # Assert exact ordered class names
        names = [type(e).__name__ for e in result]
        assert names == [
            "RunStartedRenderEvent",
            "TextStartRenderEvent",
            "TextDeltaRenderEvent",
            "TextEndRenderEvent",
            "RunFinishedRenderEvent",
        ], f"Unexpected emission order: {names}"

        assert result[0].run_id == result[-1].run_id  # run_id consistent
        assert result[-1].outcome == "success"

    async def test_emission_order_with_tool(self) -> None:
        """Tool-only turn: exact v2 ordered event sequence (B8-13, #1211 S4).

        v2 contract: RunStarted → ToolCallStart → ToolCallEnd → RunFinished.
        No v1 ToolSummaryRenderEvent post-v1 removal. ToolCallEnd is synthesised
        by the orphan-end path at ResultLlmEvent time (no ToolUseEndLlmEvent sent).
        """
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Edit", tool_id="t1", input={"path": "src/foo.py"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))

        # Assert exact ordered class names
        names = [type(e).__name__ for e in result]
        assert names == [
            "RunStartedRenderEvent",
            "ToolCallStartRenderEvent",
            "ToolCallEndRenderEvent",
            "RunFinishedRenderEvent",
        ], f"Unexpected emission order: {names}"

        assert result[0].run_id == result[-1].run_id  # run_id consistent
        assert result[-1].outcome == "success"

    async def test_run_id_matches_trace_id(self) -> None:
        """RunStarted/RunFinished both carry run_id == TraceContext.trace_id."""
        token = TraceContext.set_trace_id("abc-123")
        try:
            processor = StreamProcessor(cfg())
            events = async_events(
                TextLlmEvent(text="x"),
                ResultLlmEvent(is_error=False, duration_ms=1),
            )
            result = await collect(processor.process(events))
        finally:
            TraceContext.reset_trace_id(token)

        started = next(e for e in result if isinstance(e, RunStartedRenderEvent))
        finished = next(e for e in result if isinstance(e, RunFinishedRenderEvent))
        assert started.run_id == "abc-123"
        assert finished.run_id == "abc-123"

    async def test_run_id_synthetic_when_trace_unset(self) -> None:
        """No active TraceContext → synthetic prefix; both bookends carry it."""
        # Sanity: prior test must have cleaned up. ContextVar has no "unset"
        # token, so a missing try/finally elsewhere would leak in here.
        assert TraceContext.get_trace_id() is None, (
            "test leaked trace_id from a prior test — see test_run_id_matches_trace_id"
        )
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="x"),
            ResultLlmEvent(is_error=False, duration_ms=1),
        )
        result = await collect(processor.process(events))

        started = next(e for e in result if isinstance(e, RunStartedRenderEvent))
        finished = next(e for e in result if isinstance(e, RunFinishedRenderEvent))
        assert started.run_id.startswith("synthetic-")
        assert started.run_id == finished.run_id

    async def test_run_id_empty_trace_id_falls_back_to_synthetic(self) -> None:
        """Empty-string trace_id is falsy → synthetic fallback kicks in.

        Documents current `or` semantics in StreamProcessor.process. If the
        invariant should hold for empty strings too (run_id == ""), tighten
        the source to `is None` and update this test.
        """
        token = TraceContext.set_trace_id("")
        try:
            processor = StreamProcessor(cfg())
            events = async_events(
                TextLlmEvent(text="x"),
                ResultLlmEvent(is_error=False, duration_ms=1),
            )
            result = await collect(processor.process(events))
        finally:
            TraceContext.reset_trace_id(token)

        started = next(e for e in result if isinstance(e, RunStartedRenderEvent))
        assert started.run_id.startswith("synthetic-")

    async def test_run_error_on_input_exception(self) -> None:
        """Exception while iterating LlmEvent stream → RunError + re-raise."""

        class _Boom(RuntimeError):
            pass

        async def _raising_events():
            yield TextLlmEvent(text="partial")
            raise _Boom("input died — this string MUST NOT reach the wire")

        processor = StreamProcessor(cfg())
        seen: list[RenderEvent] = []
        with __import__("pytest").raises(_Boom):
            async for ev in processor.process(_raising_events()):
                seen.append(ev)

        # Order: RunStarted → ... → RunError (immediately before re-raise).
        assert isinstance(seen[0], RunStartedRenderEvent)
        assert isinstance(seen[-1], RunErrorRenderEvent)
        # message carries the exception class name, NOT str(exc) — str(exc)
        # can leak file paths, hostnames, auth tokens onto the wire.
        assert seen[-1].message == "_Boom"
        assert "input died" not in seen[-1].message
        # code is intentionally None per RunErrorRenderEvent docstring
        # ("reserved for a future taxonomy" — #1097 carry-over). The EventEmitter
        # translator deliberately drops SanitizedError.code on the wire.
        assert seen[-1].code is None
        assert seen[-1].run_id == seen[0].run_id
        # Position-aware: no RunFinished must appear before RunError, and
        # exactly two lifecycle events should be present (started + error).
        error_idx = next(
            i for i, e in enumerate(seen) if isinstance(e, RunErrorRenderEvent)
        )
        assert not any(
            isinstance(seen[i], RunFinishedRenderEvent) for i in range(error_idx)
        )
        lifecycle = [
            e
            for e in seen
            if isinstance(
                e,
                (
                    RunStartedRenderEvent,
                    RunFinishedRenderEvent,
                    RunErrorRenderEvent,
                ),
            )
        ]
        assert len(lifecycle) == 2  # exactly RunStarted + RunError

    async def test_emission_order_empty_stream(self) -> None:
        """Zero-event input still emits RunStarted + RunFinished bookends.

        Confirms the `if not _result_received` branch flows through the success
        path and does NOT trigger RunError (no exception raised). Pairs with
        the pre-existing `test_empty_stream` which strips lifecycle events.
        """
        processor = StreamProcessor(cfg())
        result = await collect(processor.process(async_events()))

        assert isinstance(result[0], RunStartedRenderEvent)
        assert isinstance(result[-1], RunFinishedRenderEvent)
        assert not any(isinstance(e, RunErrorRenderEvent) for e in result)

    def test_schema_versions(self) -> None:
        """Each new event type carries its own SCHEMA_VERSION_* constant."""
        from lyra.core.messaging.render_events import (
            SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT,
            SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT,
            SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT,
        )

        assert RunStartedRenderEvent(run_id="x").schema_version == (
            SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT
        )
        assert RunFinishedRenderEvent(run_id="x").schema_version == (
            SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT
        )
        assert RunErrorRenderEvent(run_id="x", message="m").schema_version == (
            SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT
        )


# ---------------------------------------------------------------------------
# Slice 3 of #1096 — ToolCall* lifecycle
# ---------------------------------------------------------------------------


class TestToolCallLifecycle:
    """ToolCall{Start,Args,End,Result} streamed events with dual-emit + dedupe."""

    async def test_emission_order_single_call(self) -> None:
        """Start → Args (×N) → End → Result, all sharing tool_call_id."""
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        events = async_events(
            ToolUseLlmEvent(tool_name="Read", tool_id="t1", input={}),
            ToolUseDeltaLlmEvent(tool_id="t1", partial_json='{"file_path":'),
            ToolUseDeltaLlmEvent(tool_id="t1", partial_json='"/tmp/x"}'),
            ToolUseEndLlmEvent(tool_id="t1"),
            ToolResultLlmEvent(tool_id="t1", content="ok"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))

        v2 = [
            e
            for e in result
            if isinstance(
                e,
                (
                    ToolCallStartRenderEvent,
                    ToolCallArgsRenderEvent,
                    ToolCallEndRenderEvent,
                    ToolCallResultRenderEvent,
                ),
            )
        ]
        types = [type(e).__name__ for e in v2]
        assert types == [
            "ToolCallStartRenderEvent",
            "ToolCallArgsRenderEvent",
            "ToolCallArgsRenderEvent",
            "ToolCallEndRenderEvent",
            "ToolCallResultRenderEvent",
        ]
        assert all(e.tool_call_id == "t1" for e in v2)

    async def test_each_tool_use_yields_one_start_event(self) -> None:
        """Each ToolUseLlmEvent the SP receives produces one ToolCallStart.

        Dedupe of CLI's dual emission (content_block_start + post-hoc
        assistant message) lives in the parser since #1100 review (A1+B1):
        wire-level artifacts stay below the application boundary. This SP
        test asserts the post-dedupe contract — each `ToolUseLlmEvent` that
        reaches `process()` corresponds to a unique tool_call. Parser-level
        dedupe is verified separately in test_cli_streaming_parse.py.
        """
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        events = async_events(
            ToolUseLlmEvent(tool_name="Glob", tool_id="t1", input={}),
            ToolUseEndLlmEvent(tool_id="t1"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        starts = [e for e in result if isinstance(e, ToolCallStartRenderEvent)]
        assert len(starts) == 1
        # T1 strengthening (#1100 review): also verify tool_call_id and
        # tool_name correlation, so a regression that dropped the wrong fields
        # would not silently pass.
        assert starts[0].tool_call_id == "t1"
        assert starts[0].tool_name == "Glob"

    async def test_orphan_end_synthesis_at_result(self) -> None:
        """Start without End triggers synthesized End at ResultLlmEvent time."""
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        events = async_events(
            ToolUseLlmEvent(tool_name="Read", tool_id="t1", input={}),
            # NO ToolUseEndLlmEvent
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        ends = [e for e in result if isinstance(e, ToolCallEndRenderEvent)]
        assert len(ends) == 1
        assert ends[0].tool_call_id == "t1"

    # B8-14: v1 removed in #1192 S3; "dual-emit" premise invalid post-cutover.
    # Removed per spec #1211.

    async def test_tool_call_args_passes_partial_json_verbatim(self) -> None:
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        events = async_events(
            ToolUseLlmEvent(tool_name="Read", tool_id="t1", input={}),
            ToolUseDeltaLlmEvent(tool_id="t1", partial_json='{"a":'),
            ToolUseDeltaLlmEvent(tool_id="t1", partial_json="1}"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        args = [e for e in result if isinstance(e, ToolCallArgsRenderEvent)]
        assert [e.delta for e in args] == ['{"a":', "1}"]

    async def test_tool_call_result_carries_is_error(self) -> None:
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        # Use a non-sensitive tool name so the S1 boundary scrubber does not
        # redact the content (Read/Bash/Edit/Write are sanitized by default).
        events = async_events(
            ToolUseLlmEvent(tool_name="Glob", tool_id="t1", input={}),
            ToolUseEndLlmEvent(tool_id="t1"),
            ToolResultLlmEvent(tool_id="t1", content="boom", is_error=True),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        results = [e for e in result if isinstance(e, ToolCallResultRenderEvent)]
        assert len(results) == 1
        assert results[0].is_error is True
        assert results[0].content == "boom"

    async def test_sensitive_tool_result_content_is_redacted(self) -> None:
        """S1 (#1100 review): tool result for sensitive tools is redacted on the bus."""
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        events = async_events(
            ToolUseLlmEvent(tool_name="Read", tool_id="t1", input={}),
            ToolUseEndLlmEvent(tool_id="t1"),
            ToolResultLlmEvent(
                tool_id="t1",
                content="aws_access_key_id=AKIA1234SECRETLEAK",
                is_error=False,
            ),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        results = [e for e in result if isinstance(e, ToolCallResultRenderEvent)]
        assert len(results) == 1
        assert "AKIA" not in results[0].content
        assert results[0].content.startswith("[redacted")

    async def test_orphan_tool_result_redacted_fail_closed(self) -> None:
        """ToolResult without prior ToolUseLlmEvent (unknown tool_name) is redacted."""
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        # No ToolUseLlmEvent → tool_name unknown → fail-closed redaction.
        events = async_events(
            ToolResultLlmEvent(tool_id="t1", content="leaked", is_error=False),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        results = [e for e in result if isinstance(e, ToolCallResultRenderEvent)]
        assert len(results) == 1
        assert results[0].content.startswith("[redacted")

    async def test_large_tool_result_content_truncated(self) -> None:
        """S3 (#1100 review): content larger than MAX_CONTENT_BYTES is truncated."""
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        large = "x" * 100_000
        events = async_events(
            ToolUseLlmEvent(tool_name="Glob", tool_id="t1", input={}),
            ToolUseEndLlmEvent(tool_id="t1"),
            ToolResultLlmEvent(tool_id="t1", content=large, is_error=False),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        results = [e for e in result if isinstance(e, ToolCallResultRenderEvent)]
        assert len(results) == 1
        assert len(results[0].content.encode("utf-8")) <= 65_536
        assert results[0].content.endswith("…[truncated]")

    async def test_multi_orphan_end_synthesis_preserves_ids(self) -> None:
        """T2 (#1100 review): two open tools both get their own synthesized End."""
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        events = async_events(
            ToolUseLlmEvent(tool_name="Glob", tool_id="t1", input={}),
            ToolUseLlmEvent(tool_name="Glob", tool_id="t2", input={}),
            # Neither tool sees ToolUseEndLlmEvent.
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        ends = [e for e in result if isinstance(e, ToolCallEndRenderEvent)]
        assert [e.tool_call_id for e in ends] == ["t1", "t2"]

    async def test_no_explicit_dispatch_silent_drop(self) -> None:
        """ToolUseDeltaLlmEvent reaches process() and is mapped, not absorbed."""
        cfg_ = ToolDisplayConfig(throttle_ms=0)
        processor = StreamProcessor(cfg_)
        events = async_events(
            ToolUseLlmEvent(tool_name="Read", tool_id="t1", input={}),
            ToolUseDeltaLlmEvent(tool_id="t1", partial_json="{}"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        # If the bare-else regressed, the delta would crash on event.is_error
        # access. Successful dispatch surfaces a ToolCallArgsRenderEvent.
        assert any(isinstance(e, ToolCallArgsRenderEvent) for e in result)


# ---------------------------------------------------------------------------
# Slice 4 of #1096 — Reasoning events (#1101) — T11
# ---------------------------------------------------------------------------

_REASONING_TYPES = (
    ReasoningStartRenderEvent,
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
)


class TestReasoning:
    """Reasoning block (Slice 4 / #1101) emission contract — SC-10..SC-13, SC-18."""

    # ------------------------------------------------------------------
    # T11-1 — Full reasoning triplet + consistent message_id (SC-10, SC-11)
    # ------------------------------------------------------------------

    async def test_thinking_emits_reasoning_triplet_with_consistent_message_id(
        self,
    ) -> None:
        """ThinkingLlmEvents produce Start, Delta×N, End all sharing one message_id."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ThinkingLlmEvent(text="a"),
            ThinkingLlmEvent(text="b"),
            TextLlmEvent(text="c"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — exactly 4 Reasoning events in order
        reasoning = [e for e in result if isinstance(e, _REASONING_TYPES)]
        assert len(reasoning) == 4
        start, delta_a, delta_b, end = reasoning
        assert isinstance(start, ReasoningStartRenderEvent)
        assert isinstance(delta_a, ReasoningDeltaRenderEvent)
        assert delta_a.delta == "a"
        assert isinstance(delta_b, ReasoningDeltaRenderEvent)
        assert delta_b.delta == "b"
        assert isinstance(end, ReasoningEndRenderEvent)
        # All 4 share the same message_id
        mid = start.message_id
        assert mid == delta_a.message_id == delta_b.message_id == end.message_id

    # ------------------------------------------------------------------
    # T11-2 — ReasoningEnd fires before any Text* event (SC-12)
    # ------------------------------------------------------------------

    async def test_reasoning_closes_on_text_transition(self) -> None:
        """ReasoningEndRenderEvent is emitted before the TextStart of a real text block.

        With χ-1 dual-emit (SC-7), v1 `TextRenderEvent(is_final=False)` also fires
        from inside the reasoning block alongside each `ReasoningDelta`. The
        invariant under test is: the TRANSITION to a real text block (signalled
        by `TextStartRenderEvent` for the v2 triplet) happens AFTER `ReasoningEnd`.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ThinkingLlmEvent(text="a"),
            ThinkingLlmEvent(text="b"),
            TextLlmEvent(text="c"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — ReasoningEnd index precedes the TextStart of the real text block
        end_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ReasoningEndRenderEvent)
        )
        first_text_start_idx = next(
            i for i, e in enumerate(result) if isinstance(e, TextStartRenderEvent)
        )
        assert end_idx < first_text_start_idx

    # ------------------------------------------------------------------
    # T11-3 — ReasoningEnd fires before ToolCall* events (SC-12)
    # ------------------------------------------------------------------

    async def test_reasoning_closes_on_tool_transition(self) -> None:
        """ReasoningEndRenderEvent is emitted before the first ToolCall* RenderEvent."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ThinkingLlmEvent(text="think"),
            ToolUseLlmEvent(tool_name="Glob", tool_id="t1", input={}),
            ToolResultLlmEvent(tool_id="t1", content="result"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — ReasoningEnd precedes first ToolCallStart
        end_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ReasoningEndRenderEvent)
        )
        first_toolcall_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ToolCallStartRenderEvent)
        )
        assert end_idx < first_toolcall_idx

    # ------------------------------------------------------------------
    # T11-4 — ReasoningEnd fires before final-text / run-end on Result (SC-12)
    # ------------------------------------------------------------------

    async def test_reasoning_closes_on_result_transition(self) -> None:
        """Thinking-only turn: ReasoningEnd fires before TextRenderEvent(is_final)."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ThinkingLlmEvent(text="think"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — ReasoningEnd precedes the final TextRenderEvent
        end_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ReasoningEndRenderEvent)
        )
        final_text_idx = next(
            i for i, e in enumerate(result) if isinstance(e, RunFinishedRenderEvent)
        )
        assert end_idx < final_text_idx

    # ------------------------------------------------------------------
    # T11-5 — ReasoningEnd fires before ToolCallArgs on Delta transition (SC-13)
    # ------------------------------------------------------------------

    async def test_reasoning_closes_on_tool_delta_transition(self) -> None:
        """Defensive: ReasoningEnd fires before ToolCallArgs even on Delta-first path.

        In practice the Anthropic wire format always emits ToolUseLlmEvent before
        ToolUseDeltaLlmEvent. This test verifies the close guard exists on the
        ToolUseDelta branch so any future re-ordering does not leave an open block.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ThinkingLlmEvent(text="think"),
            ToolUseLlmEvent(tool_name="Read", tool_id="t1", input={}),
            ToolUseDeltaLlmEvent(tool_id="t1", partial_json='{"file_path": "/x"}'),
            ToolUseEndLlmEvent(tool_id="t1"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — ReasoningEnd precedes the first ToolCallArgsRenderEvent
        end_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ReasoningEndRenderEvent)
        )
        first_args_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ToolCallArgsRenderEvent)
        )
        assert end_idx < first_args_idx

    async def test_show_intermediate_false_emits_no_reasoning_events(self) -> None:
        """show_intermediate=False → zero Reasoning* events emitted (L15).

        v2 contract (stream_processor.py:316): when show_intermediate=False,
        ThinkingLlmEvent chunks are dropped entirely via `continue` — no
        ReasoningStart, ReasoningDelta, or ReasoningEnd is emitted. The run
        still closes cleanly with RunFinishedRenderEvent. Parity with the
        active test_thinking_emits_reasoning_triplet_with_consistent_message_id
        (show_intermediate=True, default).
        """
        # Arrange — show_intermediate=False passed to StreamProcessor
        processor = StreamProcessor(cfg(), show_intermediate=False)
        events = async_events(
            ThinkingLlmEvent(text="a"),
            ThinkingLlmEvent(text="b"),
            ThinkingLlmEvent(text="c"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — zero Reasoning* events of any kind (gate drops chunks entirely)
        reasoning_events = [e for e in result if isinstance(e, _REASONING_TYPES)]
        assert reasoning_events == [], (
            f"Expected no Reasoning* events with show_intermediate=False, "
            f"got: {reasoning_events!r}"
        )

        # Assert — run still closes cleanly (thinking chunks don't cause an error)
        assert isinstance(result[-1], RunFinishedRenderEvent)
        assert not any(isinstance(e, RunErrorRenderEvent) for e in result)

    async def test_reasoning_closes_on_tool_use_end_transition(self) -> None:
        """Isolation: ReasoningEnd fires when ToolUseEnd is first non-Thinking event.

        Sequence skips ToolUse/ToolUseDelta so the ToolUseEnd branch is the only
        close-guard path exercised. Deleting `_close_reasoning_if_open` from the
        ToolUseEnd branch would leave the reasoning block open through to the
        truncated-stream orphan path — this test catches that regression.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ThinkingLlmEvent(text="think"),
            ToolUseEndLlmEvent(tool_id="t1"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — ReasoningEnd precedes ToolCallEnd (close-guard fires on this branch)
        end_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ReasoningEndRenderEvent)
        )
        tool_end_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ToolCallEndRenderEvent)
        )
        assert end_idx < tool_end_idx
        # Single block opened, single block closed (no orphan-close path)
        assert sum(1 for e in result if isinstance(e, ReasoningStartRenderEvent)) == 1
        assert sum(1 for e in result if isinstance(e, ReasoningEndRenderEvent)) == 1

    async def test_reasoning_closes_on_tool_result_transition(self) -> None:
        """Isolation: ReasoningEnd fires when ToolResult is first non-Thinking event.

        Sequence skips ToolUse/ToolUseDelta/ToolUseEnd so the ToolResult branch is
        the only close-guard path exercised. Deleting `_close_reasoning_if_open`
        from the ToolResult branch would leave the reasoning block open — this
        test catches that regression.
        """
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ThinkingLlmEvent(text="think"),
            ToolResultLlmEvent(tool_id="t1", content="ok"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — ReasoningEnd precedes ToolCallResult (close-guard fires here)
        end_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ReasoningEndRenderEvent)
        )
        tool_result_idx = next(
            i for i, e in enumerate(result) if isinstance(e, ToolCallResultRenderEvent)
        )
        assert end_idx < tool_result_idx
        # Single block opened, single block closed (no orphan-close path)
        assert sum(1 for e in result if isinstance(e, ReasoningStartRenderEvent)) == 1
        assert sum(1 for e in result if isinstance(e, ReasoningEndRenderEvent)) == 1

    # ------------------------------------------------------------------
    # T11-6 — Orphan ReasoningEnd + log.warning on exception mid-thinking (SC-18)
    # ------------------------------------------------------------------

    async def test_reasoning_orphan_close_on_exception(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exception after ThinkingLlmEvent: orphan ReasoningEnd + warning logged."""

        class _Boom(RuntimeError):
            pass

        async def _raising_events():
            yield ThinkingLlmEvent(text="partial think")
            raise _Boom("stream died")

        # Arrange
        processor = StreamProcessor(cfg())
        seen: list[RenderEvent] = []

        # Act
        with pytest.raises(_Boom):
            with caplog.at_level(logging.WARNING):
                async for ev in processor.process(_raising_events()):
                    seen.append(ev)

        # Assert — orphan ReasoningEnd emitted before RunErrorRenderEvent
        reasoning_end_idx = next(
            (i for i, e in enumerate(seen) if isinstance(e, ReasoningEndRenderEvent)),
            None,
        )
        run_error_idx = next(
            (i for i, e in enumerate(seen) if isinstance(e, RunErrorRenderEvent)), None
        )
        assert reasoning_end_idx is not None, "ReasoningEndRenderEvent not found"
        assert run_error_idx is not None, "RunErrorRenderEvent not found"
        assert reasoning_end_idx < run_error_idx
        # Warning must mention orphan close
        warning_msgs = [
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any(
            "orphan" in m.lower() and "reasoning" in m.lower() for m in warning_msgs
        ), f"Expected orphan ReasoningEnd warning, got: {warning_msgs}"

    # ------------------------------------------------------------------
    # T11-7 — Orphan ReasoningEnd + log.warning on truncated stream (SC-18)
    # ------------------------------------------------------------------

    async def test_reasoning_orphan_close_on_truncated_stream(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Truncated stream mid-thinking: orphan ReasoningEnd + warning logged."""
        # Arrange
        processor = StreamProcessor(cfg())
        # Stream ends without ResultLlmEvent
        events = async_events(
            ThinkingLlmEvent(text="partial think"),
        )

        # Act
        with caplog.at_level(logging.WARNING):
            result = await collect(processor.process(events))

        # Assert — orphan ReasoningEnd is present in emitted events
        reasoning_ends = [e for e in result if isinstance(e, ReasoningEndRenderEvent)]
        assert len(reasoning_ends) == 1
        # Warning must mention orphan close
        warning_msgs = [
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any(
            "orphan" in m.lower() and "reasoning" in m.lower() for m in warning_msgs
        ), f"Expected orphan ReasoningEnd warning, got: {warning_msgs}"

    # ------------------------------------------------------------------
    # T11-8 — DEBUG log observability (SC-18)
    # ------------------------------------------------------------------

    async def test_reasoning_log_debug_observability(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Normal thinking flow: exactly one 'reasoning block opened' and one
        'reasoning block closed' debug log, both carrying message_id context."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ThinkingLlmEvent(text="think"),
            TextLlmEvent(text="ok"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        # Act
        with caplog.at_level(logging.DEBUG):
            result = await collect(processor.process(events))

        # Assert — exactly one 'reasoning block opened' and one 'reasoning block closed'
        # Use getMessage() to interpolate %-format args (message_id is a format arg).
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]

        def _msg(r) -> str:  # type: ignore[no-untyped-def]
            return r.getMessage().lower()

        opened_records = [
            r for r in debug_records if "reasoning block opened" in _msg(r)
        ]
        closed_records = [
            r for r in debug_records if "reasoning block closed" in _msg(r)
        ]
        assert len(opened_records) == 1, (
            f"Expected 1 opened log, got: {[r.getMessage() for r in opened_records]}"
        )
        assert len(closed_records) == 1, (
            f"Expected 1 closed log, got: {[r.getMessage() for r in closed_records]}"
        )
        # Collect the message_id from emitted ReasoningStart event
        start_event = next(
            e for e in result if isinstance(e, ReasoningStartRenderEvent)
        )
        mid = start_event.message_id
        # Both log messages must reference the message_id
        assert mid in opened_records[0].getMessage(), (
            f"message_id '{mid}' not in opened log: {opened_records[0].getMessage()!r}"
        )
        assert mid in closed_records[0].getMessage(), (
            f"message_id '{mid}' not in closed log: {closed_records[0].getMessage()!r}"
        )


# ---------------------------------------------------------------------------
# Slice 2 of #1096 — v2 Text triplet (#1099) — T6
# ---------------------------------------------------------------------------


class TestTextTriplet:
    """Slice 2 (#1099) v2 Text triplet emission contract — T6 tests."""

    # ------------------------------------------------------------------
    # T6-1 — Single block ordering
    # ------------------------------------------------------------------

    async def test_text_triplet_single_block_ordering(self) -> None:
        """TextStart → TextDelta → TextEnd all share the same message_id."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="Hello world"),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — extract v2 triplet events only
        triplet = [e for e in result if isinstance(e, _TEXT_V2_TYPES)]
        assert len(triplet) == 3
        start, delta, end = triplet
        assert isinstance(start, TextStartRenderEvent)
        assert isinstance(delta, TextDeltaRenderEvent)
        assert isinstance(end, TextEndRenderEvent)
        # All three share the same message_id
        assert start.message_id == delta.message_id == end.message_id
        # TextStart must precede TextDelta which must precede TextEnd
        start_idx = result.index(start)
        delta_idx = result.index(delta)
        end_idx = result.index(end)
        assert start_idx < delta_idx < end_idx

    # ------------------------------------------------------------------
    # T6-2 — Multi-block distinct message_ids
    # ------------------------------------------------------------------

    async def test_text_triplet_multi_block_distinct_ids(self) -> None:
        """Two text blocks separated by a tool call produce 2 distinct message_ids."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="Before"),
            ToolUseLlmEvent(tool_name="Glob", tool_id="g1", input={}),
            TextLlmEvent(text="After"),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — two separate Start/Delta/End brackets
        starts = [e for e in result if isinstance(e, TextStartRenderEvent)]
        ends = [e for e in result if isinstance(e, TextEndRenderEvent)]
        deltas = [e for e in result if isinstance(e, TextDeltaRenderEvent)]
        assert len(starts) == 2
        assert len(ends) == 2
        assert len(deltas) == 2
        # Each bracket carries a distinct message_id
        id1, id2 = starts[0].message_id, starts[1].message_id
        assert id1 != id2
        # Each delta belongs to the correct bracket
        assert deltas[0].message_id == id1
        assert deltas[1].message_id == id2
        # Each end closes the correct bracket
        assert ends[0].message_id == id1
        assert ends[1].message_id == id2

    # ------------------------------------------------------------------
    # T6-3 — TextEnd emitted BEFORE RunErrorRenderEvent on exception
    # ------------------------------------------------------------------

    async def test_text_end_before_run_error_on_exception(self) -> None:
        """Exception mid-stream: TextEnd emitted before RunErrorRenderEvent."""

        class _Boom(RuntimeError):
            pass

        async def _raising_events():
            yield TextLlmEvent(text="partial chunk")
            raise _Boom("boom")

        # Arrange
        processor = StreamProcessor(cfg())
        seen: list[RenderEvent] = []

        # Act
        with pytest.raises(_Boom):
            async for ev in processor.process(_raising_events()):
                seen.append(ev)

        # Assert — TextEnd appears before RunError
        end_idx = next(
            (i for i, e in enumerate(seen) if isinstance(e, TextEndRenderEvent)), None
        )
        error_idx = next(
            (i for i, e in enumerate(seen) if isinstance(e, RunErrorRenderEvent)), None
        )
        assert end_idx is not None, "TextEndRenderEvent not found in stream"
        assert error_idx is not None, "RunErrorRenderEvent not found in stream"
        assert end_idx < error_idx

    # ------------------------------------------------------------------
    # T6-5 — No TextEnd when no text block is open
    # ------------------------------------------------------------------

    async def test_no_text_end_when_no_block_open(self) -> None:
        """ToolUseLlmEvent with no preceding text: NO TextEndRenderEvent emitted."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(tool_name="Glob", tool_id="g1", input={}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — guard clause prevents spurious TextEnd
        text_ends = [e for e in result if isinstance(e, TextEndRenderEvent)]
        assert len(text_ends) == 0
