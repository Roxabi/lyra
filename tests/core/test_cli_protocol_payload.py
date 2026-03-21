"""Tests for the session_id field in NDJSON payloads written to stdin.

Focus: send_and_read() and send_and_read_stream() must always send
``"session_id": ""`` in the payload.  Session binding is handled exclusively
by ``--resume <id>`` at spawn time (same approach as 2ndBrain).

Sending a non-empty session_id in the payload while also using --resume
caused the CLI to create a new session with parentUuid: None instead of
chaining on the existing conversation.
"""

from __future__ import annotations

import json

from lyra.core.cli_pool import _ProcessEntry
from lyra.core.cli_protocol import send_and_read, send_and_read_stream

from .conftest_cli_pool import (
    ASSISTANT_LINE,
    DEFAULT_MODEL,
    INIT_LINE,
    RESULT_LINE,
    _ndjson,
    make_fake_proc,
)

# ---------------------------------------------------------------------------
# Extra NDJSON lines for streaming path
# ---------------------------------------------------------------------------

_STREAM_INIT = _ndjson({"type": "system", "subtype": "init", "session_id": "new-sess"})
_TEXT_DELTA = _ndjson(
    {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hi"},
        },
    }
)
_STREAM_RESULT = _ndjson(
    {
        "type": "result",
        "session_id": "new-sess",
        "duration_ms": 10,
        "is_error": False,
    }
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _parse_stdin_payload(proc) -> dict:
    """Decode the first JSON object written to proc.stdin."""
    raw: bytes = proc.stdin.write.call_args[0][0]
    return json.loads(raw.decode().strip())


# ---------------------------------------------------------------------------
# Non-streaming path — send_and_read()
# ---------------------------------------------------------------------------


class TestNonStreamingPayload:
    """send_and_read() must always write session_id='' to stdin."""

    async def test_fresh_entry_sends_empty_session_id(self) -> None:
        """No existing session → payload session_id is ''."""
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        entry = _ProcessEntry(proc=proc, pool_id="p1", model_config=DEFAULT_MODEL)
        assert entry.session_id is None  # precondition

        await send_and_read(entry, "hello", "p1")

        payload = _parse_stdin_payload(proc)
        assert payload["session_id"] == ""

    async def test_resume_entry_sends_empty_session_id(self) -> None:
        """Resuming entry → payload session_id is '' even when entry has one.

        --resume <id> is passed at spawn time; the payload field must be
        empty to avoid the CLI creating a new session (parentUuid: None).
        """
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        entry = _ProcessEntry(proc=proc, pool_id="p1", model_config=DEFAULT_MODEL)
        entry.session_id = "old-session-f911cd3c"

        await send_and_read(entry, "hello", "p1")

        payload = _parse_stdin_payload(proc)
        assert payload["session_id"] == ""


# ---------------------------------------------------------------------------
# Streaming path — send_and_read_stream()
# ---------------------------------------------------------------------------


class TestStreamingPayload:
    """send_and_read_stream() must always write session_id='' to stdin."""

    async def test_fresh_entry_sends_empty_session_id(self) -> None:
        """No existing session → payload session_id is ''."""
        proc = make_fake_proc([_STREAM_INIT, _TEXT_DELTA, _STREAM_RESULT])
        entry = _ProcessEntry(proc=proc, pool_id="p1", model_config=DEFAULT_MODEL)
        assert entry.session_id is None  # precondition

        await send_and_read_stream(entry, "hello", "p1")

        payload = _parse_stdin_payload(proc)
        assert payload["session_id"] == ""

    async def test_resume_entry_sends_empty_session_id(self) -> None:
        """Resuming entry → streaming payload session_id is '' even when entry has one.

        --resume <id> is passed at spawn time; the payload field must be
        empty to avoid the CLI creating a new session (parentUuid: None).
        """
        proc = make_fake_proc([_STREAM_INIT, _TEXT_DELTA, _STREAM_RESULT])
        entry = _ProcessEntry(proc=proc, pool_id="p1", model_config=DEFAULT_MODEL)
        entry.session_id = "old-session-f911cd3c"

        await send_and_read_stream(entry, "hello", "p1")

        payload = _parse_stdin_payload(proc)
        assert payload["session_id"] == ""
