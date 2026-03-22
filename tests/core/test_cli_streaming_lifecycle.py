"""StreamingIterator error, cleanup, timeout, send_and_read_stream."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from lyra.core.cli_protocol import StreamingIterator, send_and_read_stream
from lyra.llm.events import ResultLlmEvent, TextLlmEvent

from .conftest import (
    ASSISTANT_INTERMEDIATE_LINE,
    DEFAULT_POOL_ID,
    ERROR_RESULT_LINE,
    INIT_LINE,
    RESULT_LINE,
    TEXT_DELTA_LINE,
    TEXT_DELTA_LINE2,
    _ndjson,
    make_entry,
    make_fake_proc,
)

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
        assert chunks == [ResultLlmEvent(is_error=True, duration_ms=50, cost_usd=None)]
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
        assert chunks == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

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

    async def test_session_id_in_payload_is_always_empty(self) -> None:
        # session_id must always be "" — session binding is handled by --resume at spawn
        proc = make_fake_proc([INIT_LINE, RESULT_LINE])
        entry = make_entry(proc)
        entry.session_id = "existing-session"

        await send_and_read_stream(entry, "continue", DEFAULT_POOL_ID)

        written_bytes = proc.stdin.write.call_args[0][0]
        payload = json.loads(written_bytes.decode().strip())
        assert payload["session_id"] == ""

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
        assert chunks == [
            TextLlmEvent(text="Hello"),
            TextLlmEvent(text=" world"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

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

    async def test_on_intermediate_accepted_but_deprecated(self) -> None:
        # Arrange — on_intermediate still accepted for backward compat but is deprecated
        proc = make_fake_proc(
            [INIT_LINE, ASSISTANT_INTERMEDIATE_LINE, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)
        received: list[str] = []

        async def on_intermediate(text: str) -> None:
            received.append(text)

        # Act
        it = await send_and_read_stream(
            entry, "hello", DEFAULT_POOL_ID, on_intermediate=on_intermediate
        )
        events = [ev async for ev in it]

        # Assert — callback NOT fired (deprecated); events still yielded correctly
        assert received == []
        assert events == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]

    async def test_on_intermediate_none_yields_events_normally(self) -> None:
        # Arrange — no on_intermediate; iterator must work normally
        proc = make_fake_proc(
            [INIT_LINE, ASSISTANT_INTERMEDIATE_LINE, TEXT_DELTA_LINE, RESULT_LINE]
        )
        entry = make_entry(proc)

        # Act
        it = await send_and_read_stream(entry, "hello", DEFAULT_POOL_ID)
        events = [ev async for ev in it]

        # Assert — text-only assistant event skipped; LlmEvents still yielded
        assert events == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None),
        ]
