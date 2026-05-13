"""Tests for lyra.core.processors.stream_processor — StreamProcessor (S3)."""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path
from typing import AsyncIterator

import pytest

from lyra.core.messaging.events import (
    ResultLlmEvent,
    TextLlmEvent,
    ToolResultLlmEvent,
    ToolUseDeltaLlmEvent,
    ToolUseEndLlmEvent,
    ToolUseLlmEvent,
)
from lyra.core.messaging.render_events import (
    RenderEvent,
    RunErrorRenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
    ToolSummaryRenderEvent,
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
    # T9 — Text-only turn (SC-3)
    # ------------------------------------------------------------------

    async def test_text_only(self) -> None:
        """Text-only turn: two intermediate chunks + one final TextRenderEvent."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="Hello "),
            TextLlmEvent(text="world"),
            ResultLlmEvent(is_error=False, duration_ms=100),
        )

        # Act
        result = strip_run_lifecycle(await collect(processor.process(events)))

        # Assert — each TextLlmEvent streams as is_final=False, then a single
        # is_final=True with the full accumulated text at ResultLlmEvent.
        assert len(result) == 3
        chunk1, chunk2, final = result
        assert isinstance(chunk1, TextRenderEvent)
        assert chunk1.text == "Hello "
        assert chunk1.is_final is False
        assert isinstance(chunk2, TextRenderEvent)
        assert chunk2.text == "world"
        assert chunk2.is_final is False
        assert isinstance(final, TextRenderEvent)
        assert final.text == "Hello world"
        assert final.is_final is True

    # ------------------------------------------------------------------
    # T10 — Single Edit tool call (SC-1, SC-2)
    # ------------------------------------------------------------------

    async def test_single_edit(self) -> None:
        """Single Edit with show_intermediate=True (default):
        intermediate text + final ToolSummary + TextRenderEvent.

        When intermediate text is flushed before a tool call, the mid-turn
        ToolSummaryRenderEvent is intentionally suppressed so adapters have time
        to display the text before the tool card overwrites it.  The summary is
        still emitted unconditionally by ResultLlmEvent.
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

        # Act
        result = strip_run_lifecycle(await collect(processor.process(events)))

        # Assert — 3 events: intermediate text, final summary, final text
        # Mid-turn ToolSummaryRenderEvent is suppressed when intermediate text
        # was just flushed (avoids immediately overwriting the text).
        assert len(result) == 3
        inter, final, text = result
        assert isinstance(inter, TextRenderEvent)
        assert inter.text == "Refactoring..."
        assert inter.is_final is False
        assert isinstance(final, ToolSummaryRenderEvent)
        assert final.is_complete is True
        assert isinstance(text, TextRenderEvent)
        assert text.is_final is True
        # _total_text preserves pre-tool text even after _pending_text is cleared
        assert text.text == "Refactoring..."

    async def test_single_edit_no_intermediate(self) -> None:
        """Single Edit with show_intermediate=False: text held until final event."""
        # Arrange
        processor = StreamProcessor(cfg(), show_intermediate=False)
        events = async_events(
            TextLlmEvent(text="Refactoring..."),
            ToolUseLlmEvent(
                tool_name="Edit", tool_id="t1", input={"path": "src/foo.py"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = strip_run_lifecycle(await collect(processor.process(events)))

        # Assert — 3 events: mid-turn summary, final summary, text
        # show_intermediate=False keeps text accumulated until ResultLlmEvent.
        assert len(result) == 3
        mid, final, text = result
        assert isinstance(mid, ToolSummaryRenderEvent)
        assert mid.is_complete is False
        assert isinstance(final, ToolSummaryRenderEvent)
        assert final.is_complete is True
        assert isinstance(text, TextRenderEvent)
        assert text.is_final is True
        assert text.text == "Refactoring..."

    async def test_write_tool_tracked(self) -> None:
        """Write tool calls are accumulated into the files dict."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Write", tool_id="w1", input={"path": "src/new.py"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        assert "src/new.py" in final_summaries[0].files

    # ------------------------------------------------------------------
    # T11 — Five edits at threshold (SC-4: names mode)
    # ------------------------------------------------------------------

    async def test_five_edits_at_threshold(self) -> None:
        """Exactly names_threshold edits keeps names mode (edits list populated)."""
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
        result = await collect(processor.process(events))

        # Assert — find the final ToolSummaryRenderEvent
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        final = final_summaries[0]
        assert "src/foo.py" in final.files
        entry = final.files["src/foo.py"]
        assert entry.count == 5
        assert len(entry.edits) == 5  # names mode — still at threshold

    # ------------------------------------------------------------------
    # T12 — Six edits: count mode (SC-4: threshold+1)
    # ------------------------------------------------------------------

    async def test_six_edits_count_mode(self) -> None:
        """names_threshold+1 edits switches to count mode (edits cleared)."""
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
        result = await collect(processor.process(events))

        # Assert — final summary switches to count mode
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        final = final_summaries[0]
        assert "src/foo.py" in final.files
        entry = final.files["src/foo.py"]
        assert entry.count == 6
        assert entry.edits == []  # count mode

    # ------------------------------------------------------------------
    # T13 — Two files, no group (SC-5)
    # ------------------------------------------------------------------

    async def test_two_files_no_group(self) -> None:
        """Two distinct files remain in per-file display (below group_threshold)."""
        # Arrange
        processor = StreamProcessor(cfg(group_threshold=3))
        events = async_events(
            ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={"path": "a.py"}),
            ToolUseLlmEvent(tool_name="Edit", tool_id="t2", input={"path": "b.py"}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        assert len(final_summaries[0].files) == 2
        assert "a.py" in final_summaries[0].files
        assert "b.py" in final_summaries[0].files

    # ------------------------------------------------------------------
    # T14 — Three files at group_threshold (SC-5)
    # ------------------------------------------------------------------

    async def test_three_files_group(self) -> None:
        """Three distinct files at group_threshold — all files still tracked."""
        # Arrange
        processor = StreamProcessor(cfg(group_threshold=3))
        events = async_events(
            ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={"path": "a.py"}),
            ToolUseLlmEvent(tool_name="Edit", tool_id="t2", input={"path": "b.py"}),
            ToolUseLlmEvent(tool_name="Edit", tool_id="t3", input={"path": "c.py"}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        assert len(final_summaries[0].files) == 3
        assert "a.py" in final_summaries[0].files
        assert "b.py" in final_summaries[0].files
        assert "c.py" in final_summaries[0].files

    # ------------------------------------------------------------------
    # T15 — 80 edits over 5 files (SC-4, SC-5)
    # ------------------------------------------------------------------

    async def test_eighty_tools_multi_file(self) -> None:
        """80 edits cycling 5 files: each file gets count==16 in count mode."""
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
        result = await collect(processor.process(events))

        # Assert
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        final = final_summaries[0]
        assert len(final.files) == 5
        for file_name in file_names:
            assert file_name in final.files
            entry = final.files[file_name]
            assert entry.count == 16
            assert entry.edits == []  # count mode (16 > names_threshold=3)

    # ------------------------------------------------------------------
    # T16 — Bash truncation (SC-6)
    # ------------------------------------------------------------------

    async def test_bash_truncation(self) -> None:
        """Bash commands longer than bash_max_len are truncated."""
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
        result = await collect(processor.process(events))

        # Assert
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        assert len(final_summaries[0].bash_commands) == 1
        assert len(final_summaries[0].bash_commands[0]) == 60

    # ------------------------------------------------------------------
    # T17 — Silent Read/Grep/Glob (SC-7)
    # ------------------------------------------------------------------

    async def test_silent_read_grep_glob(self) -> None:
        """Read, Grep, Glob are silent: increment counters, not visible summary."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(tool_name="Read", tool_id="r1", input={}),
            ToolUseLlmEvent(tool_name="Grep", tool_id="g1", input={}),
            ToolUseLlmEvent(tool_name="Glob", tool_id="gl1", input={}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        final = final_summaries[0]
        assert final.silent_counts.reads == 1
        assert final.silent_counts.greps == 1
        assert final.silent_counts.globs == 1
        assert final.files == {}
        assert final.bash_commands == []
        assert final.web_fetches == []
        assert final.agent_calls == []

    # ------------------------------------------------------------------
    # T18 — WebFetch visible (SC-9)
    # ------------------------------------------------------------------

    async def test_web_fetch_visible(self) -> None:
        """WebFetch calls are recorded in the web_fetches list."""
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
        result = await collect(processor.process(events))

        # Assert
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        assert len(final_summaries[0].web_fetches) == 1

    async def test_web_search_visible(self) -> None:
        """WebSearch calls are recorded in the web_fetches list."""
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
        result = await collect(processor.process(events))

        # Assert
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        assert len(final_summaries[0].web_fetches) == 1

    async def test_web_fetch_hidden_when_show_false(self) -> None:
        """WebFetch is silently dropped when show['web_fetch']=False."""
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
        result = await collect(processor.process(events))

        # Assert — event dropped: no ToolSummaryRenderEvent emitted
        tool_summaries = [e for e in result if isinstance(e, ToolSummaryRenderEvent)]
        assert len(tool_summaries) == 0

    # ------------------------------------------------------------------
    # T19 — Agent calls accumulation (SC-10)
    # ------------------------------------------------------------------

    async def test_agent_calls_accumulation(self) -> None:
        """Agent tool calls are accumulated in agent_calls list."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Agent", tool_id="a1", input={"description": "sub-task"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert
        final_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(final_summaries) == 1
        assert final_summaries[0].agent_calls == ["sub-task"]

    # ------------------------------------------------------------------
    # T20 — ResultLlmEvent bypasses throttle (SC-8)
    # ------------------------------------------------------------------

    async def test_result_bypasses_throttle(self) -> None:
        """ResultLlmEvent bypasses throttle; final ToolSummaryRenderEvent emitted."""
        # Arrange
        processor = StreamProcessor(cfg(throttle_ms=9_999_999))
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Edit", tool_id="t1", input={"path": "src/foo.py"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — a complete summary IS emitted despite huge throttle
        complete_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        assert len(complete_summaries) == 1

        # Also verify throttle does not suppress the first mid-turn event
        mid_summaries = [
            e
            for e in result
            if isinstance(e, ToolSummaryRenderEvent) and not e.is_complete
        ]
        assert len(mid_summaries) == 1

    # ------------------------------------------------------------------
    # T21 — Throttle suppresses duplicate mid-turn events (SC-8)
    # ------------------------------------------------------------------

    async def test_throttle_suppression(self) -> None:
        """Second tool within throttle window is suppressed (1 mid-turn summary)."""
        # Arrange
        processor = StreamProcessor(cfg(throttle_ms=9_999_999))
        events = async_events(
            ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={"path": "a.py"}),
            ToolUseLlmEvent(tool_name="Edit", tool_id="t2", input={"path": "b.py"}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — exactly 1 mid-turn (is_complete=False) summary
        mid_summaries = [
            e
            for e in result
            if isinstance(e, ToolSummaryRenderEvent) and not e.is_complete
        ]
        assert len(mid_summaries) == 1

    # ------------------------------------------------------------------
    # T22 — Throttle=0 passes all mid-turn events through (SC-8)
    # ------------------------------------------------------------------

    async def test_throttle_pass_through(self) -> None:
        """throttle_ms=0 disables throttling — all mid-turn summaries emitted."""
        # Arrange
        processor = StreamProcessor(cfg(throttle_ms=0))
        events = async_events(
            ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={"path": "a.py"}),
            ToolUseLlmEvent(tool_name="Edit", tool_id="t2", input={"path": "b.py"}),
            ResultLlmEvent(is_error=False, duration_ms=50),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert — exactly 2 mid-turn (is_complete=False) summaries
        mid_summaries = [
            e
            for e in result
            if isinstance(e, ToolSummaryRenderEvent) and not e.is_complete
        ]
        assert len(mid_summaries) == 2

    # ------------------------------------------------------------------
    # T23 — Text accumulation across multiple chunks (SC-2)
    # ------------------------------------------------------------------

    async def test_text_accumulation(self) -> None:
        """Chunks streamed individually; final TextRenderEvent has full concat text."""
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

        # Assert — 3 intermediate chunks + 1 final with full concatenated text.
        text_events = [e for e in result if isinstance(e, TextRenderEvent)]
        assert len(text_events) == 4
        assert text_events[-1].text == "Hello world"
        assert text_events[-1].is_final is True

    # ------------------------------------------------------------------
    # B3 — is_error propagation from ResultLlmEvent → TextRenderEvent (#392)
    # ------------------------------------------------------------------

    async def test_is_error_propagated_to_text_render_event(self) -> None:
        """ResultLlmEvent(is_error=True) → TextRenderEvent(is_error=True) (#392)."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="error response"),
            ResultLlmEvent(is_error=True, duration_ms=0),
        )

        # Act
        result = await collect(processor.process(events))
        # One intermediate (is_final=False) + one final (is_final=True).
        # is_error is only set on the final event.
        final_text = [
            e for e in result if isinstance(e, TextRenderEvent) and e.is_final
        ]

        # Assert
        assert len(final_text) == 1
        assert final_text[0].text == "error response"
        assert final_text[0].is_error is True
        assert final_text[0].is_final is True

    async def test_is_error_false_propagated_to_text_render_event(self) -> None:
        """ResultLlmEvent(is_error=False) → TextRenderEvent(is_error=False)."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="normal response"),
            ResultLlmEvent(is_error=False, duration_ms=0),
        )

        # Act
        result = await collect(processor.process(events))
        final_text = [
            e for e in result if isinstance(e, TextRenderEvent) and e.is_final
        ]

        # Assert
        assert len(final_text) == 1
        assert final_text[0].is_error is False

    async def test_error_text_surfaces_when_no_streamed_text(self) -> None:
        """ResultLlmEvent(is_error=True, error_text=...) with no streamed text
        → TextRenderEvent carries error_text so adapter can surface it.
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
        result = await collect(processor.process(events))
        text_events = [e for e in result if isinstance(e, TextRenderEvent)]

        # Assert
        assert len(text_events) == 1
        assert text_events[0].text == "Not logged in · Please run /login"
        assert text_events[0].is_error is True
        assert text_events[0].is_final is True

    async def test_streamed_text_preferred_over_error_text(self) -> None:
        """When text was streamed, prefer it over error_text (recovered tool)."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="recovered output"),
            ResultLlmEvent(
                is_error=True,
                duration_ms=100,
                error_text="should be ignored",
            ),
        )

        # Act
        result = await collect(processor.process(events))
        # Final event carries the full accumulated text, not the error_text shim.
        final_text = [
            e for e in result if isinstance(e, TextRenderEvent) and e.is_final
        ]

        # Assert
        assert len(final_text) == 1
        assert final_text[0].text == "recovered output"

    async def test_empty_stream(self) -> None:
        """Empty event stream emits a terminal error event (backend died)."""
        # Arrange
        processor = StreamProcessor(cfg())

        # Act
        result = strip_run_lifecycle(await collect(processor.process(async_events())))

        # Assert — backend produced nothing: emit an error so the "…"
        # placeholder is replaced instead of staying stuck.
        assert len(result) == 1
        assert isinstance(result[0], TextRenderEvent)
        assert result[0].is_error is True
        assert result[0].is_final is True

    async def test_no_result_event(self) -> None:
        """Stream truncated without ResultLlmEvent flushes pending state."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(TextLlmEvent(text="partial response"))

        # Act
        result = await collect(processor.process(events))

        # Assert — chunk streamed immediately as is_final=False, then truncation
        # path emits _total_text again as is_final=False to signal truncation.
        text_events = [e for e in result if isinstance(e, TextRenderEvent)]
        assert len(text_events) == 2
        assert all(e.text == "partial response" for e in text_events)
        assert all(e.is_final is False for e in text_events)

    # ------------------------------------------------------------------
    # T24 — Hexagonal boundary (no framework imports in stream_processor)
    # ------------------------------------------------------------------

    def test_hexagonal_boundary(self) -> None:
        """stream_processor.py must not import aiogram, discord, or anthropic."""
        # Arrange
        _root = Path(__file__).resolve().parent.parent.parent
        source_path = _root / "src" / "lyra" / "core" / "stream_processor.py"

        if not source_path.exists():
            import pytest

            pytest.skip("stream_processor.py not yet implemented")

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
        """Text-only turn emits RunStarted first and RunFinished last."""
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="hello"),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))

        assert isinstance(result[0], RunStartedRenderEvent)
        assert isinstance(result[-1], RunFinishedRenderEvent)
        assert result[-1].outcome == "success"
        # The text event is sandwiched between the lifecycle bookends.
        assert any(isinstance(e, TextRenderEvent) for e in result[1:-1])

    async def test_emission_order_with_tool(self) -> None:
        """Tool-using turn keeps lifecycle bookends around tool/text events."""
        processor = StreamProcessor(cfg(), show_intermediate=False)
        events = async_events(
            ToolUseLlmEvent(
                tool_name="Edit", tool_id="t1", input={"path": "src/foo.py"}
            ),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))

        assert isinstance(result[0], RunStartedRenderEvent)
        assert isinstance(result[-1], RunFinishedRenderEvent)
        # Mid-turn payload: at least one ToolSummaryRenderEvent and one TextRenderEvent.
        middle = result[1:-1]
        assert any(isinstance(e, ToolSummaryRenderEvent) for e in middle)
        assert any(isinstance(e, TextRenderEvent) for e in middle)

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

    async def test_soft_error_emits_finished_not_error(self) -> None:
        """ResultLlmEvent.is_error=True (soft error) → RunFinished, not RunError."""
        processor = StreamProcessor(cfg())
        events = async_events(
            ResultLlmEvent(is_error=True, duration_ms=10, error_text="model error"),
        )

        result = await collect(processor.process(events))

        assert isinstance(result[-1], RunFinishedRenderEvent)
        assert result[-1].outcome == "success"
        assert not any(isinstance(e, RunErrorRenderEvent) for e in result)
        # The soft error still surfaces via TextRenderEvent.is_error=True.
        text_events = [e for e in result if isinstance(e, TextRenderEvent)]
        assert any(e.is_error for e in text_events)

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
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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

    async def test_dual_emit_v1_and_v2_both_present(self) -> None:
        """T4 (#1100 review): assert BOTH v1 ToolSummary AND v2 ToolCall* are emitted.

        The dual-emit contract is umbrella-spec invariant 5 (coexistence).
        Asserting only v1 count would let a silent drop of ToolCallStart pass.
        """
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
        processor = StreamProcessor(cfg_)
        events = async_events(
            ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={"path": "src/x.py"}),
            ResultLlmEvent(is_error=False, duration_ms=10),
        )

        result = await collect(processor.process(events))
        v1_summaries = [
            e for e in result if isinstance(e, ToolSummaryRenderEvent) and e.is_complete
        ]
        v2_starts = [e for e in result if isinstance(e, ToolCallStartRenderEvent)]
        # Both halves of the dual-emit must fire for the same source event.
        assert len(v1_summaries) == 1
        assert len(v2_starts) == 1

    async def test_tool_call_args_passes_partial_json_verbatim(self) -> None:
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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
        cfg_ = ToolDisplayConfig(throttle_window=0.0)
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
# Slice 2 of #1096 — v2 Text triplet (#1099) — T6
# ---------------------------------------------------------------------------

# Normalization helpers re-imported from the capture script (T1) so parity
# assertions apply identical transforms before diffing against the fixture.
from tools.capture_v1_text_baseline import normalize_event_dict  # noqa: E402


def _v1_filter(events: list[RenderEvent]) -> list[RenderEvent]:
    """Drop Text{Start,Delta,End} triplet events from a live run output.

    Mirrors the T6 parity filter: the baseline fixture was captured on this
    branch (which already includes ToolCall* events from Slice 3), so only the
    Slice 2 additive v2 Text triplet events are filtered out before diffing.
    """
    return [e for e in events if not isinstance(e, _TEXT_V2_TYPES)]


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
    # T6-4 — TextEnd emitted BEFORE v1 fallback on truncation
    # ------------------------------------------------------------------

    async def test_text_end_before_v1_fallback_on_truncation(self) -> None:
        """Truncation path: TextEnd emits before the v1 fallback TextRenderEvent.

        show_intermediate=False so no per-chunk TextRenderEvent is emitted;
        the only TextRenderEvent is the fallback from the truncation branch.
        Invariant: TextEnd closes the block before that fallback fires.
        """
        # Arrange — show_intermediate=False so no per-chunk TextRenderEvent is emitted
        processor = StreamProcessor(cfg(), show_intermediate=False)
        events = async_events(TextLlmEvent(text="partial response"))

        # Act
        result = await collect(processor.process(events))

        # Assert — TextEnd precedes the v1 fallback TextRenderEvent
        end_idx = next(
            (i for i, e in enumerate(result) if isinstance(e, TextEndRenderEvent)), None
        )
        v1_text_indices = [
            i for i, e in enumerate(result) if isinstance(e, TextRenderEvent)
        ]
        assert end_idx is not None, "TextEndRenderEvent not found"
        assert v1_text_indices, "No TextRenderEvent found"
        # The v1 fallback text event(s) all come after the TextEnd close
        assert all(end_idx < v1_idx for v1_idx in v1_text_indices)

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

    # ------------------------------------------------------------------
    # T6-6 — v1 parity against baseline fixture
    # ------------------------------------------------------------------

    async def test_v1_parity_against_baseline_fixture(self) -> None:
        """Live run filtered to v1-only events must be byte-equal to captured baseline.

        Filter rule: drop TextStart/Delta/End and ToolCall* from live output,
        then compare serialized dicts to the fixture captured by T1 (pre-T5).
        Normalization: run_id values replaced with '<run_id>' via the same regex
        used by tools/capture_v1_text_baseline.py::normalize_event_dict().
        """
        # Arrange — load fixture
        fixture_path = (
            Path(__file__).parent / "fixtures" / "v1_text_stream_baseline.json"
        )
        baseline = json.loads(fixture_path.read_text())

        config = ToolDisplayConfig(throttle_ms=0)

        async def _run(events_in) -> list[dict]:
            """Run StreamProcessor and return normalized v1-only event dicts."""
            sp = StreamProcessor(config, show_intermediate=False)
            raw: list[RenderEvent] = []
            async for ev in sp.process(events_in):
                raw.append(ev)
            v1_only = _v1_filter(raw)
            return [
                normalize_event_dict(
                    {**dataclasses.asdict(e), "type": type(e).__name__}
                )
                for e in v1_only
            ]

        # --- Scenario: single_block ---
        single_result = await _run(
            async_events(
                TextLlmEvent(text="Hello "),
                TextLlmEvent(text="world."),
                ResultLlmEvent(is_error=False, duration_ms=100),
            )
        )
        assert single_result == baseline["single_block"], (
            f"single_block mismatch:\n  got:      {single_result}\n"
            f"  expected: {baseline['single_block']}"
        )

        # --- Scenario: multi_block ---
        multi_result = await _run(
            async_events(
                TextLlmEvent(text="Before tool "),
                ToolUseLlmEvent(
                    tool_name="bash", tool_id="tool-abc123", input={"command": "ls"}
                ),
                ToolUseEndLlmEvent(tool_id="tool-abc123"),
                TextLlmEvent(text="After tool."),
                ResultLlmEvent(is_error=False, duration_ms=200),
            )
        )
        assert multi_result == baseline["multi_block"], (
            f"multi_block mismatch:\n  got:      {multi_result}\n"
            f"  expected: {baseline['multi_block']}"
        )

        # --- Scenario: error ---
        error_result = await _run(
            async_events(
                TextLlmEvent(text="Partial "),
                ResultLlmEvent(
                    is_error=True, duration_ms=50, error_text="Something went wrong"
                ),
            )
        )
        assert error_result == baseline["error"], (
            f"error mismatch:\n  got:      {error_result}\n"
            f"  expected: {baseline['error']}"
        )
