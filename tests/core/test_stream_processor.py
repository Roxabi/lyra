"""Tests for lyra.core.stream_processor — StreamProcessor (S3)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import AsyncIterator

from lyra.core.render_events import (
    TextRenderEvent,
    ToolSummaryRenderEvent,
)
from lyra.core.stream_processor import StreamProcessor
from lyra.core.tool_display_config import ToolDisplayConfig
from lyra.llm.events import ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent

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


def cfg(**kw) -> ToolDisplayConfig:
    """Base config: throttle_ms=0 (disabled). Override via kw."""
    defaults = dict(
        names_threshold=3, group_threshold=3, bash_max_len=60, throttle_ms=0
    )
    return ToolDisplayConfig(**{**defaults, **kw})  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# T9 — T24: StreamProcessor integration tests
# ---------------------------------------------------------------------------


class TestStreamProcessor:
    """StreamProcessor integration tests (SC-1 through SC-10)."""

    # ------------------------------------------------------------------
    # T9 — Text-only turn (SC-3)
    # ------------------------------------------------------------------

    async def test_text_only(self) -> None:
        """Text-only turn: one TextRenderEvent, no ToolSummaryRenderEvent."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(
            TextLlmEvent(text="Hello "),
            TextLlmEvent(text="world"),
            ResultLlmEvent(is_error=False, duration_ms=100),
        )

        # Act
        result = await collect(processor.process(events))

        # Assert
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, TextRenderEvent)
        assert event.text == "Hello world"
        assert event.is_final is True

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
        result = await collect(processor.process(events))

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
        assert text.text == ""  # pending_text was flushed before the tool call

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
        result = await collect(processor.process(events))

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
        config = ToolDisplayConfig.from_dict({"show": {"web_fetch": False}})
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
        """Multiple TextLlmEvent chunks are concatenated into one TextRenderEvent."""
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

        # Assert
        text_events = [e for e in result if isinstance(e, TextRenderEvent)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello world"

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
        text_events = [e for e in result if isinstance(e, TextRenderEvent)]

        # Assert
        assert len(text_events) == 1
        assert text_events[0].text == "error response"
        assert text_events[0].is_error is True
        assert text_events[0].is_final is True

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
        text_events = [e for e in result if isinstance(e, TextRenderEvent)]

        # Assert
        assert len(text_events) == 1
        assert text_events[0].is_error is False

    async def test_empty_stream(self) -> None:
        """Empty event stream yields no events."""
        # Arrange
        processor = StreamProcessor(cfg())

        # Act
        result = await collect(processor.process(async_events()))

        # Assert
        assert result == []

    async def test_no_result_event(self) -> None:
        """Stream truncated without ResultLlmEvent flushes pending state."""
        # Arrange
        processor = StreamProcessor(cfg())
        events = async_events(TextLlmEvent(text="partial response"))

        # Act
        result = await collect(processor.process(events))

        # Assert — pending text emitted with is_final=False to signal truncation
        text_events = [e for e in result if isinstance(e, TextRenderEvent)]
        assert len(text_events) == 1
        assert text_events[0].text == "partial response"
        assert text_events[0].is_final is False

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
