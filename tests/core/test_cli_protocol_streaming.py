"""Tests for StreamingIterator and send_and_read_stream in lyra.core.cli_protocol."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent_config import ModelConfig
from lyra.core.cli_pool import _ProcessEntry
from lyra.core.cli_protocol import StreamingIterator, send_and_read_stream

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


def make_fake_proc(stdout_lines: list[bytes]) -> MagicMock:
    """Return a mock Process with controllable stdout readline side-effects."""
    proc = MagicMock()
    proc.returncode = None  # alive
    proc.pid = 99

    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock(return_value=None)

    lines_with_eof = list(stdout_lines) + [b""]
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(side_effect=lines_with_eof)

    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    proc.kill = MagicMock()

    return proc


def make_entry(proc: MagicMock, pool_id: str = "pool-test") -> _ProcessEntry:
    return _ProcessEntry(proc=proc, pool_id=pool_id, model_config=ModelConfig())


DEFAULT_POOL_ID = "pool-stream"

INIT_LINE = _ndjson(
    {"type": "system", "subtype": "init", "session_id": "abc-123"}
)
TEXT_DELTA_LINE = _ndjson(
    {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hello"},
        },
    }
)
TEXT_DELTA_LINE2 = _ndjson(
    {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": " world"},
        },
    }
)
INPUT_JSON_DELTA_LINE = _ndjson(
    {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"key":'},
        },
    }
)
RESULT_LINE = _ndjson(
    {
        "type": "result",
        "session_id": "abc-123",
        "duration_ms": 100,
        "is_error": False,
    }
)
ERROR_RESULT_LINE = _ndjson(
    {
        "type": "result",
        "session_id": "abc-123",
        "result": "Something went wrong",
        "is_error": True,
        "subtype": "api_error",
        "duration_ms": 50,
    }
)


# ---------------------------------------------------------------------------
# TestStreamingIteratorYieldsText
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
# TestStreamingIteratorError
# ---------------------------------------------------------------------------


class TestStreamingIteratorError:
    """StreamingIterator.error reflects result is_error flag."""

    async def test_error_set_when_result_is_error_true(self) -> None:
        # Arrange
        proc = make_fake_proc([INIT_LINE, ERROR_RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert
        assert chunks == []
        assert it.error == "Something went wrong"

    async def test_error_none_on_success(self) -> None:
        # Arrange
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        async for _ in it:
            pass

        # Assert
        assert it.error is None

    async def test_error_uses_subtype_when_result_empty(self) -> None:
        # Arrange — is_error=True but result field is empty; error falls back to subtype
        error_no_result = _ndjson(
            {
                "type": "result",
                "session_id": "abc-123",
                "result": "",
                "is_error": True,
                "subtype": "rate_limit_error",
                "duration_ms": 10,
            }
        )
        proc = make_fake_proc([INIT_LINE, error_no_result])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID)
        async for _ in it:
            pass

        # Assert
        assert it.error == "rate_limit_error"


# ---------------------------------------------------------------------------
# TestStreamingIteratorCleanup
# ---------------------------------------------------------------------------


class TestStreamingIteratorCleanup:
    """StreamingIterator.aclose() and _cleanup() behaviour."""

    async def test_aclose_calls_pool_reset_fn(self) -> None:
        # Arrange
        pool_reset_fn = AsyncMock()
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE])
        entry = make_entry(proc)

        # Act — close before consuming all output
        it = StreamingIterator(entry, DEFAULT_POOL_ID, pool_reset_fn=pool_reset_fn)
        await it.aclose()

        # Assert
        pool_reset_fn.assert_awaited_once()

    async def test_aclose_noop_after_full_consumption(self) -> None:
        # Arrange — iterator fully consumed; reset_fn should NOT be called
        pool_reset_fn = AsyncMock()
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID, pool_reset_fn=pool_reset_fn)
        async for _ in it:
            pass
        await it.aclose()

        # Assert — _done was set by result event; _cleanup skips reset_fn
        pool_reset_fn.assert_not_awaited()

    async def test_aclose_idempotent(self) -> None:
        # Arrange
        pool_reset_fn = AsyncMock()
        proc = make_fake_proc([INIT_LINE])
        entry = make_entry(proc)

        # Act — call aclose twice
        it = StreamingIterator(entry, DEFAULT_POOL_ID, pool_reset_fn=pool_reset_fn)
        await it.aclose()
        await it.aclose()

        # Assert — reset_fn called only once (second call skipped due to _done)
        pool_reset_fn.assert_awaited_once()

    async def test_pool_reset_fn_exception_does_not_propagate(self) -> None:
        # Arrange — reset_fn raises; aclose must swallow it
        pool_reset_fn = AsyncMock(side_effect=RuntimeError("reset failed"))
        proc = make_fake_proc([INIT_LINE])
        entry = make_entry(proc)

        # Act / Assert — no exception propagated
        it = StreamingIterator(entry, DEFAULT_POOL_ID, pool_reset_fn=pool_reset_fn)
        await it.aclose()  # must not raise


# ---------------------------------------------------------------------------
# TestStreamingIteratorTimeout
# ---------------------------------------------------------------------------


class TestStreamingIteratorTimeout:
    """StreamingIterator timeout handling."""

    async def test_timeout_exhaustion_calls_cleanup(self) -> None:
        # Arrange — readline raises TimeoutError 3 times (exhausting retries)
        pool_reset_fn = AsyncMock()
        proc = MagicMock()
        proc.returncode = None  # alive throughout
        proc.stdout = MagicMock()
        proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(
            entry, DEFAULT_POOL_ID, pool_reset_fn=pool_reset_fn, default_timeout=0.001
        )
        chunks = [chunk async for chunk in it]

        # Assert — iterator terminated, cleanup called
        assert chunks == []
        assert it._done is True
        pool_reset_fn.assert_awaited_once()

    async def test_single_timeout_retried(self) -> None:
        # Arrange — one TimeoutError then a valid line (alive process)
        proc = MagicMock()
        proc.returncode = None
        proc.stdout = MagicMock()
        proc.stdout.readline = AsyncMock(
            side_effect=[asyncio.TimeoutError, TEXT_DELTA_LINE, RESULT_LINE, b""]
        )
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID, default_timeout=0.001)
        chunks = [chunk async for chunk in it]

        # Assert — recovered after single timeout
        assert chunks == ["Hello"]

    async def test_timeout_with_dead_process_stops_immediately(self) -> None:
        # Arrange — timeout occurs and process has died (returncode set)
        proc = MagicMock()
        proc.returncode = 1  # dead
        proc.stdout = MagicMock()
        proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
        entry = make_entry(proc)

        # Act
        it = StreamingIterator(entry, DEFAULT_POOL_ID, default_timeout=0.001)
        chunks = [chunk async for chunk in it]

        # Assert — immediate stop on dead process
        assert chunks == []
        assert it._done is True


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
# TestSendAndReadStream
# ---------------------------------------------------------------------------


class TestSendAndReadStream:
    """send_and_read_stream writes stdin and returns a StreamingIterator."""

    async def test_returns_streaming_iterator(self) -> None:
        # Arrange
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        it = await send_and_read_stream(entry, "hello", DEFAULT_POOL_ID)

        # Assert
        assert isinstance(it, StreamingIterator)

    async def test_writes_payload_to_stdin(self) -> None:
        # Arrange
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE, RESULT_LINE])
        entry = make_entry(proc)

        # Act
        await send_and_read_stream(entry, "hello world", DEFAULT_POOL_ID)

        # Assert — stdin.write called with NDJSON containing user message
        proc.stdin.write.assert_called_once()
        written_bytes: bytes = proc.stdin.write.call_args[0][0]
        payload = json.loads(written_bytes.decode().strip())
        assert payload["type"] == "user"
        assert payload["message"]["content"] == "hello world"
        assert payload["message"]["role"] == "user"

    async def test_includes_session_id_in_payload(self) -> None:
        # Arrange — entry has an existing session_id
        proc = make_fake_proc([INIT_LINE, RESULT_LINE])
        entry = make_entry(proc)
        entry.session_id = "existing-session"

        # Act
        await send_and_read_stream(entry, "continue", DEFAULT_POOL_ID)

        # Assert — payload carries the existing session_id
        written_bytes = proc.stdin.write.call_args[0][0]
        payload = json.loads(written_bytes.decode().strip())
        assert payload["session_id"] == "existing-session"

    async def test_returns_done_iterator_when_stdin_is_none(self) -> None:
        # Arrange — proc.stdin is None (simulates broken process)
        proc = make_fake_proc([])
        proc.stdin = None
        entry = make_entry(proc)

        # Act
        it = await send_and_read_stream(entry, "hello", DEFAULT_POOL_ID)

        # Assert — iterator is pre-done (no stdin → can't write)
        assert isinstance(it, StreamingIterator)
        assert it._done is True

    async def test_returns_done_iterator_on_drain_timeout(self) -> None:
        # Arrange — stdin.drain() times out
        proc = make_fake_proc([])
        proc.stdin.drain = AsyncMock(side_effect=asyncio.TimeoutError)
        entry = make_entry(proc)
        pool_reset_fn = AsyncMock()

        # Act
        it = await send_and_read_stream(
            entry, "hello", DEFAULT_POOL_ID, pool_reset_fn=pool_reset_fn
        )

        # Assert — iterator is done (drain failed → write incomplete)
        assert isinstance(it, StreamingIterator)
        assert it._done is True

    async def test_returned_iterator_yields_chunks(self) -> None:
        # Arrange
        proc = make_fake_proc(
            [INIT_LINE, TEXT_DELTA_LINE, TEXT_DELTA_LINE2, RESULT_LINE]
        )
        entry = make_entry(proc)

        # Act
        it = await send_and_read_stream(entry, "hello", DEFAULT_POOL_ID)
        chunks = [chunk async for chunk in it]

        # Assert
        assert chunks == ["Hello", " world"]

    async def test_passes_pool_reset_fn_to_iterator(self) -> None:
        # Arrange
        pool_reset_fn = AsyncMock()
        proc = make_fake_proc([INIT_LINE, TEXT_DELTA_LINE])
        entry = make_entry(proc)

        # Act
        it = await send_and_read_stream(
            entry, "hello", DEFAULT_POOL_ID, pool_reset_fn=pool_reset_fn
        )
        await it.aclose()

        # Assert — reset_fn wired through correctly
        pool_reset_fn.assert_awaited_once()
