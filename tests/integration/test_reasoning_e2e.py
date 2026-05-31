"""E2E integration test: full reasoning pipeline from NDJSON fixture to callbacks.

Spec trace: SC-19, SC-20 (Issue #1101, Slice 4).
Phase: RED-GATE final — exercises parser → StreamProcessor → StreamingSession dispatch.

Mocked (externals only):
  - OutboundFormatter (injectable MagicMock — all methods are AsyncMock/MagicMock)

Real (not mocked):
  - CliStreamingParser  (core.cli.cli_streaming_parser)
  - StreamProcessor     (core.processors.stream_processor)
  - _run_event_loop  (outbound._emitter_run)
  - ReasoningStart/Delta/EndRenderEvent  (core.messaging.render_events)
  - ThinkingLlmEvent  (core.messaging.events)

Migrated in S7 (#1501): PlatformCallbacks dataclass replaced with
MagicMock OutboundFormatter (OutboundFormatter Protocol).
"""

# pyright: reportAttributeAccessIssue=false, reportInvalidTypeForm=false
# v1 stub classes are typed as Any (see DEBT:v1-stubs below) — skipped tests
# still reference v1-shape attrs; rewrite for v2 deferred (#1192 S3 follow-up).

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.cli.cli_streaming_parser import CliStreamingParser
from lyra.core.messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
)
from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
    RenderEvent,
)
from lyra.core.processors.stream_processor import StreamProcessor
from lyra.outbound._emitter_run import _run_event_loop
from lyra.outbound.emitter import OutboundEmitter as StreamingSession

# DEBT:v1-stubs — for skipped tests; rewrite for v2 (#1192 S3 follow-up)
# Typed as Any so pyright doesn't flag v1-shape access in skipped tests.
TextRenderEvent: Any = type("TextRenderEvent", (), {})
ToolSummaryRenderEvent: Any = type("ToolSummaryRenderEvent", (), {})


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "cli_traces"
    / "thinking_high_effort.ndjson"
)

_POOL_ID = "pool-e2e-reasoning"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture_lines() -> list[str]:
    """Read the fixture file and return non-comment, non-blank NDJSON lines."""
    raw = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    return [line for line in raw if line.strip() and not line.startswith("#")]


def _parse_fixture_to_llm_events() -> list[LlmEvent]:
    """Feed all fixture lines through CliStreamingParser; return all emitted events."""
    parser = CliStreamingParser(pool_id=_POOL_ID)
    events: list[LlmEvent] = []
    for line in _load_fixture_lines():
        pending = parser.parse_line(line)
        while pending:
            events.append(pending.popleft())
    return events


async def _async_seq(*items: LlmEvent) -> AsyncIterator[LlmEvent]:
    """Yield items as an async iterator (minimal helper — no deque needed)."""
    for item in items:
        yield item


async def _collect_render_events(llm_events: list[LlmEvent]) -> list[RenderEvent]:
    """Drive StreamProcessor and collect all emitted RenderEvents."""
    sp = StreamProcessor()
    return [ev async for ev in sp.process(_async_seq(*llm_events))]


def _make_formatter(**overrides: object) -> MagicMock:
    """Build a mock OutboundFormatter with async/sync mock defaults."""
    fmt = MagicMock()
    fmt.placeholder_text = MagicMock(return_value="…")
    fmt.chunk = MagicMock(side_effect=lambda t: [t] if t else [])
    fmt.get_msg = MagicMock(side_effect=lambda key, fallback: fallback)
    fmt.send_placeholder = AsyncMock(return_value=(object(), 42))
    fmt.edit_placeholder_text = AsyncMock()
    fmt.send_trace_placeholder = AsyncMock(return_value=(object(), 43))
    fmt.send_message = AsyncMock(return_value=99)
    fmt.send_fallback = AsyncMock(return_value=77)
    fmt.edit_reasoning = AsyncMock()
    fmt.edit_tool_recap = AsyncMock()
    for k, v in overrides.items():
        setattr(fmt, k, v)
    return fmt


# ---------------------------------------------------------------------------
# Test 1: full pipeline emits reasoning then text
# ---------------------------------------------------------------------------


class TestFullPipelineEmitsReasoningThenText:
    """SC-19: parser → StreamProcessor → correct reasoning + text event sequence."""

    @pytest.mark.skip(reason="v1 removed in #1192 S3 — rewrite for v2 deferred")
    async def test_full_pipeline_emits_reasoning_then_text(self) -> None:
        # Arrange — parse fixture file end-to-end
        llm_events = _parse_fixture_to_llm_events()
        assert llm_events, "fixture must yield at least one LlmEvent"

        # Collect expected thinking texts from fixture for concatenation assertion
        expected_deltas: list[str] = []
        for line in _load_fixture_lines():
            try:
                data = json.loads(line)
                ev = data.get("event", {})
                delta = ev.get("delta", {})
                if (
                    ev.get("type") == "content_block_delta"
                    and delta.get("type") == "thinking_delta"
                ):
                    expected_deltas.append(delta["thinking"])
            except (json.JSONDecodeError, KeyError):
                pass

        # Act
        render_events = await _collect_render_events(llm_events)

        # Extract reasoning events
        reasoning_starts = [
            e for e in render_events if isinstance(e, ReasoningStartRenderEvent)
        ]
        reasoning_deltas = [
            e for e in render_events if isinstance(e, ReasoningDeltaRenderEvent)
        ]
        reasoning_ends = [
            e for e in render_events if isinstance(e, ReasoningEndRenderEvent)
        ]
        # Assert — SC-19: exactly 1 Start, 3 Delta, 1 End
        assert len(reasoning_starts) == 1, (
            f"expected 1 ReasoningStartRenderEvent, got {len(reasoning_starts)}"
        )
        assert len(reasoning_deltas) >= 3, (
            f"expected >= 3 ReasoningDeltaRenderEvent, got {len(reasoning_deltas)}"
        )
        assert len(reasoning_ends) == 1, (
            f"expected 1 ReasoningEndRenderEvent, got {len(reasoning_ends)}"
        )

        # Assert — all 3 share the same message_id
        shared_id = reasoning_starts[0].message_id
        assert all(e.message_id == shared_id for e in reasoning_deltas), (
            "all ReasoningDelta events must share the same message_id as ReasoningStart"
        )
        assert reasoning_ends[0].message_id == shared_id, (
            "ReasoningEnd message_id must match ReasoningStart"
        )

        # Assert — concatenated deltas match fixture thinking texts
        concatenated = "".join(e.delta for e in reasoning_deltas)
        assert concatenated == "".join(expected_deltas), (
            "concatenated delta strings must match fixture thinking_delta.thinking"
        )

        # Assert — ReasoningEnd fires BEFORE the first final TextRenderEvent
        end_idx = render_events.index(reasoning_ends[0])
        first_final_text_idx = next(
            (
                i
                for i, e in enumerate(render_events)
                if isinstance(e, TextRenderEvent) and e.is_final
            ),
            None,
        )
        assert first_final_text_idx is not None, (
            "fixture must produce a final TextRenderEvent"
        )
        assert end_idx < first_final_text_idx, (
            f"ReasoningEnd (idx={end_idx}) must precede final TextRenderEvent"
            f" (idx={first_final_text_idx})"
        )


# ---------------------------------------------------------------------------
# Test 2: no ThinkingLlmEvent → no Reasoning* events emitted
# ---------------------------------------------------------------------------


class TestEffortNoneEmitsNoReasoning:
    """SC-19 negative: a stream with no ThinkingLlmEvent emits zero Reasoning events."""

    async def test_effort_none_emits_no_reasoning(self) -> None:
        # Arrange — synthetic stream: text + result only (no thinking blocks)
        llm_events: list[LlmEvent] = [
            TextLlmEvent(text="The answer is 42."),
            ResultLlmEvent(is_error=False, duration_ms=50),
        ]

        # Act
        render_events = await _collect_render_events(llm_events)

        # Assert — no Reasoning* events
        reasoning_events = [
            e
            for e in render_events
            if isinstance(
                e,
                ReasoningStartRenderEvent
                | ReasoningDeltaRenderEvent
                | ReasoningEndRenderEvent,
            )
        ]
        assert reasoning_events == [], (
            "expected no Reasoning* events for a text-only stream,"
            f" got: {reasoning_events!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: callback invoked through dispatch ladder
# ---------------------------------------------------------------------------


class TestCallbackInvokedThroughDispatch:
    """SC-20: _run_event_loop routes Reasoning events to edit_reasoning on formatter."""

    async def test_callback_invoked_through_dispatch(self) -> None:
        # Arrange — parse fixture through full pipeline to get RenderEvents
        llm_events = _parse_fixture_to_llm_events()
        render_events = await _collect_render_events(llm_events)

        # Count expected reasoning events from the render stream
        expected_reasoning_events = [
            e
            for e in render_events
            if isinstance(
                e,
                ReasoningStartRenderEvent
                | ReasoningDeltaRenderEvent
                | ReasoningEndRenderEvent,
            )
        ]
        assert expected_reasoning_events, (
            "fixture must produce at least one Reasoning event"
        )

        # Build formatter mock with spy on edit_reasoning
        edit_reasoning_spy = AsyncMock()
        placeholder_obj = object()
        fmt = _make_formatter(
            send_placeholder=AsyncMock(return_value=(placeholder_obj, 42)),
            edit_reasoning=edit_reasoning_spy,
        )

        # Replay render events through StreamingSession._run_event_loop
        async def _render_stream() -> AsyncIterator[RenderEvent]:
            for ev in render_events:
                yield ev

        session = StreamingSession(fmt, outbound=None)
        await _run_event_loop(session, _render_stream(), placeholder_obj)

        # Assert — edit_reasoning called once per Reasoning* event, in order
        assert edit_reasoning_spy.call_count == len(expected_reasoning_events), (
            f"edit_reasoning spy called {edit_reasoning_spy.call_count} times, "
            f"expected {len(expected_reasoning_events)}"
        )

        # Assert — events passed are the correct types in the correct order
        call_events = [call.args[1] for call in edit_reasoning_spy.call_args_list]
        assert isinstance(call_events[0], ReasoningStartRenderEvent), (
            "first call should be ReasoningStartRenderEvent,"
            f" got {type(call_events[0])}"
        )
        assert all(
            isinstance(e, ReasoningDeltaRenderEvent) for e in call_events[1:-1]
        ), "middle calls should all be ReasoningDeltaRenderEvent"
        assert isinstance(call_events[-1], ReasoningEndRenderEvent), (
            f"last call should be ReasoningEndRenderEvent, got {type(call_events[-1])}"
        )
