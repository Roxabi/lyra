"""Tests for StreamingIterator parsing behaviour (yields, session_id, non-JSON)."""

from __future__ import annotations

import pytest

from lyra.core.cli_protocol import StreamingIterator

from .conftest import (
    ASSISTANT_INTERMEDIATE_LINE,
    ASSISTANT_INTERMEDIATE_LINE2,
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
        assert chunks == ["Hello", " world"]

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
        assert chunks == ["Hello", " world"]

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
        assert chunks == ["Hello"]

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
        assert chunks == ["Hello"]

    async def test_stops_on_eof(self) -> None:
        # Arrange — no result event; proc sends EOF
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE])  # EOF appended by helper
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert — EOF gracefully ends iteration
        assert chunks == ["Hello"]

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
        assert chunks == ["Hello"]

    async def test_skips_blank_lines(self) -> None:
        # Arrange — blank lines between events
        proc = make_fake_proc([INIT_LINE, b"\n", b"  \n", TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert
        assert chunks == ["Hello"]


# ---------------------------------------------------------------------------
# TestStreamingIteratorIntermediate
# ---------------------------------------------------------------------------


class TestStreamingIteratorIntermediate:
    """StreamingIterator fires on_intermediate for assistant events before streaming."""

    async def test_single_assistant_event_fires_callback(self) -> None:
        # Arrange — one intermediate turn, then streaming tokens
        proc = make_fake_proc(
            [INIT_LINE, ASSISTANT_INTERMEDIATE_LINE, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)
        received: list[str] = []

        async def on_intermediate(text: str) -> None:
            received.append(text)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID, on_intermediate=on_intermediate)
        chunks = [chunk async for chunk in it]

        # Assert — callback fired with intermediate text; tokens still yielded
        assert received == ["I need to check something first."]
        assert chunks == ["Hello"]

    async def test_multiple_assistant_events_fire_multiple_callbacks(self) -> None:
        # Arrange — two intermediate turns, then streaming tokens
        proc = make_fake_proc(
            [
                INIT_LINE,
                ASSISTANT_INTERMEDIATE_LINE,
                ASSISTANT_INTERMEDIATE_LINE2,
                TEXT_DELTA_LINE,
                RESULT_LINE,
            ]
        )
        entry = make_entry(proc)
        received: list[str] = []

        async def on_intermediate(text: str) -> None:
            received.append(text)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID, on_intermediate=on_intermediate)
        chunks = [chunk async for chunk in it]

        # Assert — both intermediates fired in order; tokens still yielded
        assert received == [
            "I need to check something first.",
            "Let me verify that too.",
        ]
        assert chunks == ["Hello"]

    async def test_no_callback_when_on_intermediate_is_none(self) -> None:
        # Arrange — assistant event present but no callback wired
        proc = make_fake_proc(
            [INIT_LINE, ASSISTANT_INTERMEDIATE_LINE, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)

        # Act — no on_intermediate; must not raise
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert — tokens still yielded without error
        assert chunks == ["Hello"]

    async def test_callback_exception_does_not_stop_iteration(self) -> None:
        # Arrange — callback raises; iterator must survive and still yield chunks
        proc = make_fake_proc(
            [INIT_LINE, ASSISTANT_INTERMEDIATE_LINE, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)

        async def on_intermediate(text: str) -> None:
            raise RuntimeError("callback exploded")

        # Act / Assert — no exception propagated to caller
        it = StreamingIterator(entry, DEFAULT_POOL_ID, on_intermediate=on_intermediate)
        chunks = [chunk async for chunk in it]

        assert chunks == ["Hello"]

    async def test_callback_timeout_does_not_stop_iteration(self) -> None:
        # Arrange — callback hangs longer than the 5s guard
        import asyncio

        proc = make_fake_proc(
            [INIT_LINE, ASSISTANT_INTERMEDIATE_LINE, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)

        async def on_intermediate(text: str) -> None:
            raise asyncio.TimeoutError  # simulates hanging callback

        # Act / Assert — timeout swallowed; stream continues
        it = StreamingIterator(entry, DEFAULT_POOL_ID, on_intermediate=on_intermediate)
        chunks = [chunk async for chunk in it]

        assert chunks == ["Hello"]

    async def test_no_intermediates_without_assistant_events(self) -> None:
        # Arrange — plain single-turn response: no assistant events before tokens
        proc = make_fake_proc(
            [INIT_LINE, TEXT_DELTA_LINE, TEXT_DELTA_LINE2, RESULT_LINE]
        )
        entry = make_entry(proc)
        received: list[str] = []

        async def on_intermediate(text: str) -> None:
            received.append(text)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID, on_intermediate=on_intermediate)
        chunks = [chunk async for chunk in it]

        # Assert — callback never fired; all tokens yielded
        assert received == []
        assert chunks == ["Hello", " world"]

    async def test_assistant_event_with_multiple_text_blocks_joined(self) -> None:
        # Arrange — assistant message has two text content blocks
        multi_block_line = _ndjson(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Part one."},
                        {"type": "text", "text": "Part two."},
                    ],
                },
            }
        )
        proc = make_fake_proc(
            [INIT_LINE, multi_block_line, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)
        received: list[str] = []

        async def on_intermediate(text: str) -> None:
            received.append(text)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID, on_intermediate=on_intermediate)
        chunks = [chunk async for chunk in it]

        # Assert — text blocks joined with double newline
        assert received == ["Part one.\n\nPart two."]
        assert chunks == ["Hello"]

    async def test_assistant_event_with_no_text_blocks_skipped(self) -> None:
        # Arrange — assistant message has only tool_use blocks (no text)
        tool_use_line = _ndjson(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash"}],
                },
            }
        )
        proc = make_fake_proc([INIT_LINE, tool_use_line, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)
        received: list[str] = []

        async def on_intermediate(text: str) -> None:
            received.append(text)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID, on_intermediate=on_intermediate)
        chunks = [chunk async for chunk in it]

        # Assert — no-text assistant event silently skipped; tokens still yielded
        assert received == []
        assert chunks == ["Hello"]
