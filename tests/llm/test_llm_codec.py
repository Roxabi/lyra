"""LlmCodec layer tests — encode + decode + decode_chunk.

Spec § Slice S4. Pure unit tests: no pool, no transport, no NATS.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from factory.core.agent.agent_config import ModelConfig
from factory.core.messaging.events import ResultLlmEvent, TextLlmEvent
from factory.llm.llm_codec import LlmCodec
from factory.transport._result import Err, Ok, SanitizedError

CONTRACT_VERSION = "1"

_MODEL = ModelConfig(backend="nats", model="claude-sonnet-4-6")


def _ok_response(
    *, ok: bool = True, text: str = "world", error: str | None = None
) -> Ok[bytes]:
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": "t1",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "request_id": "r1",
    }
    if text:
        payload["text"] = text
    if error:
        payload["error"] = error
    return Ok(json.dumps(payload).encode())


def _ok_chunk(
    *,
    delta: str | None,
    done: bool,
    is_error: bool = False,
    duration_ms: int | None = None,
) -> Ok[bytes]:
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": "t1",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "request_id": "req1",
        "delta": delta,
        "done": done,
        "is_error": is_error,
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    return Ok(json.dumps(payload).encode())


class TestLlmCodecEncode:
    def test_encode_empty_messages_produces_single_user_message(self) -> None:
        codec = LlmCodec()
        payload, trace_id = codec.encode("hello", _MODEL, "sys", None, stream=False)
        body = json.loads(payload)
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert isinstance(trace_id, str) and len(trace_id) > 0

    def test_encode_passthrough_when_last_message_matches_text(self) -> None:
        codec = LlmCodec()
        messages = [{"role": "user", "content": "hello"}]
        payload, _ = codec.encode("hello", _MODEL, "sys", messages, stream=False)
        body = json.loads(payload)
        assert body["messages"] == messages

    def test_encode_appends_when_last_message_differs_from_text(self) -> None:
        codec = LlmCodec()
        messages = [{"role": "user", "content": "first"}]
        payload, _ = codec.encode("second", _MODEL, "sys", messages, stream=False)
        body = json.loads(payload)
        assert len(body["messages"]) == 2
        assert body["messages"][-1] == {"role": "user", "content": "second"}

    def test_encode_propagates_stream_true(self) -> None:
        codec = LlmCodec()
        payload, _ = codec.encode("x", _MODEL, "sys", None, stream=True)
        body = json.loads(payload)
        assert body["stream"] is True

    def test_encode_propagates_stream_false(self) -> None:
        codec = LlmCodec()
        payload, _ = codec.encode("x", _MODEL, "sys", None, stream=False)
        body = json.loads(payload)
        assert body.get("stream") is False

    def test_encode_returns_unique_trace_ids(self) -> None:
        codec = LlmCodec()
        _, t1 = codec.encode("x", _MODEL, "sys", None, stream=False)
        _, t2 = codec.encode("x", _MODEL, "sys", None, stream=False)
        assert t1 != t2


class TestLlmCodecDecode:
    def test_decode_ok_returns_populated_llm_result(self) -> None:
        codec = LlmCodec()
        result = codec.decode(_ok_response(ok=True, text="world"), trace_id="t1")
        assert result.error == ""
        assert result.result == "world"
        assert result.session_id == "t1"

    def test_decode_err_maps_sanitized_error_to_llm_result(self) -> None:
        codec = LlmCodec()
        err = SanitizedError(
            code="transport.timeout", message="TimeoutError", retryable=True
        )
        result = codec.decode(Err(err), trace_id="t1")
        assert result.error == "TimeoutError"
        assert result.retryable is True
        assert result.worker_error is not None
        assert result.worker_error.code == "transport.timeout"

    def test_decode_err_non_retryable_propagated(self) -> None:
        codec = LlmCodec()
        err = SanitizedError(
            code="transport.parse", message="ParseError", retryable=False
        )
        result = codec.decode(Err(err), trace_id="t1")
        assert result.retryable is False
        assert result.worker_error is not None

    def test_decode_invalid_bytes_returns_validation_error_not_raises(self) -> None:
        codec = LlmCodec()
        result = codec.decode(Ok(b"not-json"), trace_id="t1")
        assert result.error != ""
        assert (
            "validation" in result.error.lower()
            or result.error == "decode.validation_error"
        )

    def test_decode_ok_false_propagates_error_field(self) -> None:
        codec = LlmCodec()
        result = codec.decode(
            _ok_response(ok=False, text="", error="model fail"), trace_id="t1"
        )
        assert result.error == "model fail"
        assert result.worker_error is not None

    def test_decode_ok_false_without_error_field_uses_fallback(self) -> None:
        # Negative test: resp.ok=False with no error field → fallback message used
        codec = LlmCodec()
        payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "trace_id": "t1",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "request_id": "r1",
        }
        result = codec.decode(Ok(json.dumps(payload).encode()), trace_id="t1")
        assert result.error != ""
        assert result.worker_error is not None


class TestLlmCodecDecodeChunk:
    def test_decode_chunk_err_returns_terminal_result_event(self) -> None:
        codec = LlmCodec()
        err = SanitizedError(
            code="transport.timeout", message="TimeoutError", retryable=True
        )
        event = codec.decode_chunk(Err(err))
        assert isinstance(event, ResultLlmEvent)
        assert event.is_error is True

    def test_decode_chunk_invalid_bytes_returns_terminal_event_not_raises(self) -> None:
        codec = LlmCodec()
        event = codec.decode_chunk(Ok(b"not-json"))
        assert isinstance(event, ResultLlmEvent)
        assert event.is_error is True

    def test_decode_chunk_text_delta_returns_text_event(self) -> None:
        codec = LlmCodec()
        event = codec.decode_chunk(_ok_chunk(delta="hello", done=False))
        assert isinstance(event, TextLlmEvent)
        assert event.text == "hello"

    def test_decode_chunk_done_returns_terminal_result_event_not_error(self) -> None:
        codec = LlmCodec()
        event = codec.decode_chunk(_ok_chunk(delta=None, done=True, duration_ms=42))
        assert isinstance(event, ResultLlmEvent)
        assert event.is_error is False
        assert event.duration_ms == 42

    def test_decode_chunk_is_error_true_returns_error_result_event(self) -> None:
        codec = LlmCodec()
        payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "trace_id": "t1",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "request_id": "req1",
            "delta": None,
            "done": False,
            "is_error": True,
            "error": "model exploded",
        }
        event = codec.decode_chunk(Ok(json.dumps(payload).encode()))
        assert isinstance(event, ResultLlmEvent)
        assert event.is_error is True

    def test_decode_chunk_no_delta_no_done_returns_none(self) -> None:
        codec = LlmCodec()
        payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "trace_id": "t1",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "request_id": "req1",
            "delta": None,
            "done": False,
            "is_error": False,
        }
        event = codec.decode_chunk(Ok(json.dumps(payload).encode()))
        assert event is None
