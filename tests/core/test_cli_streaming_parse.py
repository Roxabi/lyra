"""Tests for StreamingIterator parsing behaviour (yields, session_id, non-JSON)."""

from __future__ import annotations

import pytest

from lyra.core.cli.cli_protocol import StreamingIterator
from lyra.core.messaging.events import (
    ResultLlmEvent,
    TextLlmEvent,
    ToolResultLlmEvent,
    ToolUseDeltaLlmEvent,
    ToolUseEndLlmEvent,
    ToolUseLlmEvent,
)

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


def _tool_use_start_line(
    index: int = 1, tool_id: str = "toolu_AB", name: str = "Read"
) -> bytes:
    return _ndjson(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": name,
                    "input": {},
                },
            },
        }
    )


def _input_json_delta_line(index: int = 1, partial_json: str = '{"key":') -> bytes:
    return _ndjson(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": partial_json},
            },
        }
    )


def _content_block_stop_line(index: int = 1) -> bytes:
    return _ndjson(
        {
            "type": "stream_event",
            "event": {"type": "content_block_stop", "index": index},
        }
    )


def _tool_result_user_line(
    tool_use_id: str = "toolu_AB",
    content: object = "ok",
    is_error: bool = False,
) -> bytes:
    return _ndjson(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content,
                        "is_error": is_error,
                    }
                ],
            },
        }
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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
        ]

    async def test_brace_shaped_malformed_json_emits_cli_parse_envelope(self) -> None:
        """Spec C4 path (b): a `{`-shaped line that fails to decode emits cli.parse.

        A truncated NDJSON line (e.g. CLI subprocess died mid-stream) is
        unambiguous protocol corruption — the parser must emit a terminal
        ResultLlmEvent with `worker_error.code == "cli.parse"` so the hub
        instrumentation chain activates instead of silently swallowing the
        failure.
        """
        # Arrange — `{`-shaped but invalid JSON simulates a truncated result line
        truncated = b'{"type": "result", "session_id":\n'
        proc = make_fake_proc([INIT_LINE, truncated])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert — exactly one terminal ResultLlmEvent with cli.parse
        assert len(chunks) == 1
        result = chunks[0]
        assert isinstance(result, ResultLlmEvent)
        assert result.is_error is True
        assert result.worker_error is not None
        assert result.worker_error.code == "cli.parse"
        assert result.worker_error.retryable is False
        # error_text reuses the (scrubbed) WorkerError.message — never raw str(exc)
        assert result.error_text == result.worker_error.message


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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
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
        proc = make_fake_proc([INIT_LINE, cb_start_line, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — ToolUseLlmEvent before TextLlmEvent, then ResultLlmEvent
        assert events == [
            ToolUseLlmEvent(tool_name="Read", tool_id="tu_42", input={}),
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
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
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
        ]


# ---------------------------------------------------------------------------
# TestStreamingIteratorToolDeltas — Slice 3 of #1096
# ---------------------------------------------------------------------------


class TestStreamingIteratorToolDeltas:
    """Parser surfaces input_json_delta + content_block_stop + tool_result."""

    async def test_input_json_delta_after_tool_use_start_yields_delta_event(
        self,
    ) -> None:
        # Arrange — content_block_start tool_use opens index=1; subsequent
        # input_json_delta on the same index yields ToolUseDeltaLlmEvent.
        proc = make_fake_proc(
            [
                INIT_LINE,
                _tool_use_start_line(index=1, tool_id="toolu_AB", name="Read"),
                _input_json_delta_line(index=1, partial_json='{"file_path":'),
                _input_json_delta_line(index=1, partial_json='"/tmp/x"}'),
                _content_block_stop_line(index=1),
                RESULT_LINE,
            ]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — Start, Delta, Delta, End, Result, all sharing tool_id
        assert events == [
            ToolUseLlmEvent(tool_name="Read", tool_id="toolu_AB", input={}),
            ToolUseDeltaLlmEvent(tool_id="toolu_AB", partial_json='{"file_path":'),
            ToolUseDeltaLlmEvent(tool_id="toolu_AB", partial_json='"/tmp/x"}'),
            ToolUseEndLlmEvent(tool_id="toolu_AB"),
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
        ]

    async def test_input_json_delta_without_open_tool_block_silently_skipped(
        self,
    ) -> None:
        # Arrange — input_json_delta arrives without a prior content_block_start;
        # parser must silently drop (existing pre-Slice-3 behaviour preserved).
        proc = make_fake_proc(
            [INIT_LINE, INPUT_JSON_DELTA_LINE, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert
        assert events == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
        ]

    async def test_empty_partial_json_delta_skipped(self) -> None:
        # Arrange — empty partial_json should not yield a delta event.
        proc = make_fake_proc(
            [
                INIT_LINE,
                _tool_use_start_line(),
                _input_json_delta_line(partial_json=""),
                RESULT_LINE,
            ]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — no ToolUseDeltaLlmEvent
        assert events == [
            ToolUseLlmEvent(tool_name="Read", tool_id="toolu_AB", input={}),
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
        ]

    async def test_content_block_stop_unknown_index_silent(self) -> None:
        # Arrange — content_block_stop for a text block (no open tool block at
        # index=0) must not emit ToolUseEndLlmEvent.
        proc = make_fake_proc(
            [INIT_LINE, _content_block_stop_line(index=0), TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert
        assert events == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
        ]

    async def test_user_message_tool_result_yields_event(self) -> None:
        # Arrange — user message with tool_result block.
        proc = make_fake_proc(
            [
                INIT_LINE,
                _tool_use_start_line(),
                _content_block_stop_line(),
                _tool_result_user_line(content="file contents…", is_error=False),
                RESULT_LINE,
            ]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert
        assert events == [
            ToolUseLlmEvent(tool_name="Read", tool_id="toolu_AB", input={}),
            ToolUseEndLlmEvent(tool_id="toolu_AB"),
            ToolResultLlmEvent(
                tool_id="toolu_AB", content="file contents…", is_error=False
            ),
            ResultLlmEvent(is_error=False, duration_ms=100, session_id="abc-123"),
        ]

    async def test_user_message_tool_result_error_propagates(self) -> None:
        # Arrange — is_error=True flows through verbatim.
        proc = make_fake_proc(
            [
                INIT_LINE,
                _tool_use_start_line(),
                _tool_result_user_line(content="boom", is_error=True),
                RESULT_LINE,
            ]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert
        assert any(
            isinstance(e, ToolResultLlmEvent) and e.is_error and e.content == "boom"
            for e in events
        )

    async def test_user_message_tool_result_list_content_concatenates(self) -> None:
        # Arrange — content can be a list of typed blocks; parser concatenates
        # text-only with placeholders for non-text.
        proc = make_fake_proc(
            [
                INIT_LINE,
                _tool_use_start_line(),
                _tool_result_user_line(
                    content=[
                        {"type": "text", "text": "first"},
                        {"type": "image", "data": "..."},
                        {"type": "text", "text": "third"},
                    ],
                    is_error=False,
                ),
                RESULT_LINE,
            ]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — non-text block placeholdered as [image]
        results = [e for e in events if isinstance(e, ToolResultLlmEvent)]
        assert len(results) == 1
        assert results[0].content == "first[image]third"

    async def test_concurrent_tool_calls_interleaved_deltas(self) -> None:
        """T3 (#1100 review): two simultaneous tool blocks at distinct indices,
        interleaved deltas — each delta routes to its own tool_id.
        """
        proc = make_fake_proc(
            [
                INIT_LINE,
                _tool_use_start_line(index=1, tool_id="t1", name="Read"),
                _tool_use_start_line(index=2, tool_id="t2", name="Bash"),
                _input_json_delta_line(index=2, partial_json='{"cmd":'),
                _input_json_delta_line(index=1, partial_json='{"path":'),
                _input_json_delta_line(index=2, partial_json='"ls"}'),
                _input_json_delta_line(index=1, partial_json='"/tmp"}'),
                _content_block_stop_line(index=1),
                _content_block_stop_line(index=2),
                RESULT_LINE,
            ]
        )
        entry = make_entry(proc)

        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        deltas = [e for e in events if isinstance(e, ToolUseDeltaLlmEvent)]
        # Each delta must route to the correct tool_id by index, not by order.
        assert [(e.tool_id, e.partial_json) for e in deltas] == [
            ("t2", '{"cmd":'),
            ("t1", '{"path":'),
            ("t2", '"ls"}'),
            ("t1", '"/tmp"}'),
        ]

        ends = [e for e in events if isinstance(e, ToolUseEndLlmEvent)]
        assert [e.tool_id for e in ends] == ["t1", "t2"]

    async def test_dedupe_tool_use_across_streaming_and_post_hoc(self) -> None:
        """B1+A1 (#1100 review): parser dedupes the dual emission of tool_use.

        CLI emits tool_use once at content_block_start and again post-hoc in
        the assistant message. Parser must announce each tool_id exactly once.
        """
        post_hoc_line = _ndjson(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_AB",
                            "name": "Read",
                            "input": {"file_path": "/tmp/x"},
                        }
                    ],
                },
            }
        )
        proc = make_fake_proc(
            [
                INIT_LINE,
                _tool_use_start_line(index=1, tool_id="toolu_AB", name="Read"),
                post_hoc_line,
                _content_block_stop_line(index=1),
                RESULT_LINE,
            ]
        )
        entry = make_entry(proc)

        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        starts = [e for e in events if isinstance(e, ToolUseLlmEvent)]
        # Exactly one ToolUseLlmEvent for toolu_AB despite both wire paths.
        assert len(starts) == 1
        assert starts[0].tool_id == "toolu_AB"
        assert starts[0].tool_name == "Read"
