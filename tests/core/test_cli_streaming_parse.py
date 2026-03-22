"""Tests for StreamingIterator parsing behaviour (yields, session_id, non-JSON)."""

from __future__ import annotations

import pytest

from lyra.core.cli_protocol import StreamingIterator
from lyra.llm.events import ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent

from .conftest import (
    ASSISTANT_INTERMEDIATE_LINE,
    DEFAULT_POOL_ID,
    INIT_LINE,
    INPUT_JSON_DELTA_LINE,
    RESULT_LINE,
    TEXT_DELTA_LINE,
    TEXT_DELTA_LINE2,
    _ndjson,
    make_entry,
    make_fake_proc,
)

# ---------------------------------------------------------------------------
# TestStreamingIteratorYields
# ---------------------------------------------------------------------------


class TestStreamingIteratorYields:
    """StreamingIterator yields text_delta chunks from content_block_delta events."""

    async def test_yields_text_delta_chunks(self) -> None:
        # Arrange
        proc = make_fake_proc(
            [INIT_LINE, TEXT_DELTA_LINE, TEXT_DELTA_LINE2, RESULT_LINE]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert
        assert chunks == [
            TextLlmEvent(text="Hello"),
            TextLlmEvent(text=" world"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_skips_input_json_delta_events(self) -> None:
        # Arrange — mix of text_delta and input_json_delta; only text_delta should yield
        proc = make_fake_proc(
            [
                INIT_LINE,
                TEXT_DELTA_LINE,
                INPUT_JSON_DELTA_LINE,
                TEXT_DELTA_LINE2,
                RESULT_LINE,
            ]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert — input_json_delta silently skipped
        assert chunks == [
            TextLlmEvent(text="Hello"),
            TextLlmEvent(text=" world"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_skips_empty_text_delta(self) -> None:
        # Arrange — text_delta with empty string should not be yielded
        empty_delta = _ndjson(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": ""},
                },
            }
        )
        proc = make_fake_proc([INIT_LINE, empty_delta, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert
        assert chunks == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_stops_on_result_event(self) -> None:
        # Arrange — result event must terminate iteration
        extra_delta = _ndjson(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "After result"},
                },
            }
        )
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE, RESULT_LINE, extra_delta])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert — stops at result; extra_delta not yielded
        assert chunks == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_stops_on_eof(self) -> None:
        # Arrange — no result event; proc sends EOF
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE])  # EOF appended by helper
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert — EOF gracefully ends iteration with NO ResultLlmEvent
        assert chunks == [TextLlmEvent(text="Hello")]

    async def test_already_done_raises_stop_async_iteration(self) -> None:
        # Arrange — create iterator and exhaust it
        proc = make_fake_proc([RESULT_LINE])
        entry = make_entry(proc)
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        it._done = True

        # Act / Assert
        with pytest.raises(StopAsyncIteration):
            await it.__anext__()


# ---------------------------------------------------------------------------
# TestStreamingIteratorSessionId
# ---------------------------------------------------------------------------


class TestStreamingIteratorSessionId:
    """StreamingIterator captures and exposes session_id."""

    async def test_session_id_captured_from_system_init(self) -> None:
        # Arrange
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        async for _ in it:
            pass

        # Assert
        assert it.session_id == "abc-123"

    async def test_session_id_updated_from_result_event(self) -> None:
        # Arrange — result carries a different session_id (session_id from result)
        result_with_session = _ndjson(
            {
                "type": "result",
                "session_id": "result-sess-999",
                "duration_ms": 10,
                "is_error": False,
            }
        )
        proc = make_fake_proc([TEXT_DELTA_LINE, result_with_session])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        async for _ in it:
            pass

        # Assert — session_id from result event overwrites None
        assert it.session_id == "result-sess-999"

    async def test_session_id_none_when_closed_before_result(self) -> None:
        # Arrange — close iterator before result arrives
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE])
        entry = make_entry(proc)

        # Act — close before consuming
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        await it.aclose()

        # Assert — no session_id was set before close
        assert it.session_id is None

    async def test_session_id_propagated_to_entry(self) -> None:
        # Arrange
        proc = make_fake_proc([INIT_LINE, RESULT_LINE])
        entry = make_entry(proc)
        assert entry.session_id is None

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        async for _ in it:
            pass

        # Assert — entry.session_id updated on init
        assert entry.session_id == "abc-123"


# ---------------------------------------------------------------------------
# TestStreamingIteratorNonJson
# ---------------------------------------------------------------------------


class TestStreamingIteratorNonJson:
    """StreamingIterator skips non-JSON lines gracefully."""

    async def test_skips_non_json_lines(self) -> None:
        # Arrange — inject a non-JSON line between valid events
        non_json = b"[DEBUG] some internal log\n"
        proc = make_fake_proc([INIT_LINE, non_json, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert — non-JSON line skipped; text_delta still yielded
        assert chunks == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_skips_blank_lines(self) -> None:
        # Arrange — blank lines between events
        proc = make_fake_proc([INIT_LINE, b"\n", b"  \n", TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert
        assert chunks == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]


# ---------------------------------------------------------------------------
# TestStreamingIteratorAssistant
# ---------------------------------------------------------------------------


class TestStreamingIteratorAssistant:
    """StreamingIterator assistant event handling in the new LlmEvent model."""

    async def test_text_only_assistant_event_silently_skipped(self) -> None:
        # Arrange — assistant message with text-only content is silently skipped
        # (only stream_event text_deltas yield TextLlmEvent; no ToolUseLlmEvent)
        proc = make_fake_proc(
            [INIT_LINE, ASSISTANT_INTERMEDIATE_LINE, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — text_delta yields TextLlmEvent; intermediate text-only block dropped
        assert events == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_tool_use_in_assistant_event_yields_tool_use_event(self) -> None:
        # Arrange — assistant block contains a tool_use entry → ToolUseLlmEvent emitted
        tool_use_line = _ndjson(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Bash",
                            "input": {"cmd": "ls"},
                        }
                    ],
                },
            }
        )
        proc = make_fake_proc([INIT_LINE, tool_use_line, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — ToolUseLlmEvent before TextLlmEvent, then ResultLlmEvent
        assert events == [
            ToolUseLlmEvent(tool_name="Bash", tool_id="t1", input={"cmd": "ls"}),
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_multiple_tool_use_blocks_yield_multiple_events(self) -> None:
        # Arrange — two tool_use blocks in one assistant message
        multi_tool_line = _ndjson(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                        {"type": "tool_use", "id": "t2", "name": "Write", "input": {}},
                    ],
                },
            }
        )
        proc = make_fake_proc([INIT_LINE, multi_tool_line, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — both ToolUseLlmEvent emitted before ResultLlmEvent
        assert events == [
            ToolUseLlmEvent(tool_name="Read", tool_id="t1", input={}),
            ToolUseLlmEvent(tool_name="Write", tool_id="t2", input={}),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_stream_event_content_block_start_tool_use_yields_event(
        self,
    ) -> None:
        # Arrange — stream_event / content_block_start with type=tool_use
        # exercises the branch at cli_protocol.py:StreamingIterator.__anext__
        cb_start_line = _ndjson(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "content_block": {
                        "type": "tool_use",
                        "name": "Read",
                        "id": "tu_42",
                    },
                },
            }
        )
        proc = make_fake_proc(
            [INIT_LINE, cb_start_line, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — ToolUseLlmEvent before TextLlmEvent, then ResultLlmEvent
        assert events == [
            ToolUseLlmEvent(tool_name="Read", tool_id="tu_42", input={}),
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_on_intermediate_deprecated_but_harmless(self, caplog) -> None:
        # Arrange — on_intermediate is deprecated; passing it should log a warning
        # but not raise and not affect iteration
        import logging

        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        async def cb(text: str) -> None:
            pass

        # Act
        with caplog.at_level(logging.WARNING):
            it = StreamingIterator(entry, DEFAULT_POOL_ID, on_intermediate=cb)
        events = [ev async for ev in it]

        # Assert — warning logged, events still correct
        assert any("deprecated" in r.message.lower() for r in caplog.records)
        assert events == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_no_intermediates_without_assistant_events(self) -> None:
        # Arrange — plain single-turn response: no assistant events, just stream deltas
        proc = make_fake_proc(
            [INIT_LINE, TEXT_DELTA_LINE, TEXT_DELTA_LINE2, RESULT_LINE]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — two TextLlmEvent then ResultLlmEvent, no ToolUseLlmEvent
        assert events == [
            TextLlmEvent(text="Hello"),
            TextLlmEvent(text=" world"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]
