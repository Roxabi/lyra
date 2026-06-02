"""CliPoolCodec — decode_chunk unit tests.

Focuses on the WorkerError.code validation guard introduced in #1502:
unregistered codes must be mapped to the fallback, registered codes must
pass through unchanged.  decode_chunk must never raise.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from factory.core.messaging.events import ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent
from factory.llm.cli_pool_codec import CliPoolCodec
from factory.transport._result import Err, Ok, SanitizedError

CONTRACT_VERSION = "1"
_TRACE_ID = "trace-test-1"
_POOL_ID = "pool-1"

_codec = CliPoolCodec()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk(**kwargs: Any) -> Ok[bytes]:
    """Build a minimal CliChunkEvent payload wrapped in Ok[bytes]."""
    base: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": _TRACE_ID,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "pool_id": _POOL_ID,
        "event_type": "text",
        "is_error": False,
        "done": False,
    }
    base.update(kwargs)
    return Ok(json.dumps(base).encode())


def _worker_error_dict(
    code: str, message: str = "boom", retryable: bool = True
) -> dict:
    return {"code": code, "message": message, "retryable": retryable}


# ---------------------------------------------------------------------------
# Tests — KNOWN_CODES validation (the fix)
# ---------------------------------------------------------------------------


class TestValidateWorkerErrorCode:
    def test_unregistered_code_in_error_branch_maps_to_fallback(self) -> None:
        """Error branch: chunk.is_error=True with an unregistered code → fallback."""
        payload = _chunk(
            event_type="error",
            is_error=True,
            worker_error=_worker_error_dict("worker.bogus_future", "future error"),
        )
        event = _codec.decode_chunk(payload)
        assert isinstance(event, ResultLlmEvent)
        assert event.is_error is True
        assert event.worker_error is not None
        assert event.worker_error.code == "worker.internal"
        # original message must be preserved
        assert event.worker_error.message == "future error"

    def test_registered_code_in_error_branch_passes_through(self) -> None:
        """Error branch: registered code passes through unchanged."""
        payload = _chunk(
            event_type="error",
            is_error=True,
            worker_error=_worker_error_dict("worker.crash", "crashed"),
        )
        event = _codec.decode_chunk(payload)
        assert isinstance(event, ResultLlmEvent)
        assert event.worker_error is not None
        assert event.worker_error.code == "worker.crash"
        assert event.worker_error.message == "crashed"

    def test_unregistered_code_in_result_branch_maps_to_fallback(self) -> None:
        """Result branch: event_type=result with an unregistered code → fallback."""
        payload = _chunk(
            event_type="result",
            done=True,
            is_error=False,
            worker_error=_worker_error_dict(
                "worker.bogus_future", "from result branch"
            ),
        )
        event = _codec.decode_chunk(payload)
        assert isinstance(event, ResultLlmEvent)
        assert event.worker_error is not None
        assert event.worker_error.code == "worker.internal"
        assert event.worker_error.message == "from result branch"

    def test_registered_code_in_result_branch_passes_through(self) -> None:
        """Result branch: registered code passes through unchanged."""
        payload = _chunk(
            event_type="result",
            done=True,
            is_error=False,
            worker_error=_worker_error_dict("worker.internal", "internal error"),
        )
        event = _codec.decode_chunk(payload)
        assert isinstance(event, ResultLlmEvent)
        assert event.worker_error is not None
        assert event.worker_error.code == "worker.internal"

    def test_none_worker_error_passes_through(self) -> None:
        """None worker_error is valid (no error) — must not be substituted."""
        payload = _chunk(event_type="result", done=True)
        event = _codec.decode_chunk(payload)
        assert isinstance(event, ResultLlmEvent)
        assert event.worker_error is None

    def test_unregistered_code_preserves_retryable_and_detail(self) -> None:
        """Fallback mapping must preserve retryable and detail fields."""
        we_dict = {
            "code": "worker.totally_unknown",
            "message": "details matter",
            "retryable": False,
            "detail": "extra context",
        }
        payload = _chunk(event_type="error", is_error=True, worker_error=we_dict)
        event = _codec.decode_chunk(payload)
        assert isinstance(event, ResultLlmEvent)
        assert event.worker_error is not None
        assert event.worker_error.code == "worker.internal"
        assert event.worker_error.retryable is False
        assert event.worker_error.detail == "extra context"


# ---------------------------------------------------------------------------
# Tests — decode_chunk never raises
# ---------------------------------------------------------------------------


class TestDecodeChunkNeverRaises:
    def test_err_result_returns_terminal_event(self) -> None:
        err = SanitizedError(
            code="transport.timeout", message="timed out", retryable=True
        )
        event = _codec.decode_chunk(Err(err))
        assert isinstance(event, ResultLlmEvent)
        assert event.is_error is True

    def test_invalid_json_returns_terminal_event_not_raises(self) -> None:
        event = _codec.decode_chunk(Ok(b"not-json"))
        assert isinstance(event, ResultLlmEvent)
        assert event.is_error is True

    def test_text_chunk_returns_text_event(self) -> None:
        payload = _chunk(event_type="text", text="hello world")
        event = _codec.decode_chunk(payload)
        assert isinstance(event, TextLlmEvent)
        assert event.text == "hello world"

    def test_tool_use_chunk_returns_tool_use_event(self) -> None:
        payload = _chunk(
            event_type="tool_use",
            tool_name="bash",
            tool_id="tid-1",
            tool_input={"cmd": "ls"},
        )
        event = _codec.decode_chunk(payload)
        assert isinstance(event, ToolUseLlmEvent)
        assert event.tool_name == "bash"

    def test_session_id_chunk_returns_none(self) -> None:
        payload = _chunk(event_type="session_id", session_id="sess-abc")
        event = _codec.decode_chunk(payload)
        assert event is None

    def test_done_true_returns_terminal_result_event(self) -> None:
        # done=True on event_type="result" triggers the result branch
        payload = _chunk(event_type="result", done=True)
        event = _codec.decode_chunk(payload)
        assert isinstance(event, ResultLlmEvent)
        assert event.is_error is False
