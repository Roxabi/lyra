"""Roundtrip + invariant tests for LLM-domain contract models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from roxabi_contracts.llm import LlmChunkEvent, LlmRequest, LlmResponse

_ENV: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-trace",
    "issued_at": datetime(2026, 4, 28, tzinfo=timezone.utc),
}

_MESSAGES = [{"role": "user", "content": "hello"}]


def test_llm_request_roundtrip() -> None:
    req = LlmRequest(**_ENV, request_id="req-1", messages=_MESSAGES)
    restored = LlmRequest.model_validate_json(req.model_dump_json())
    assert restored.request_id == "req-1"
    assert restored.messages == _MESSAGES
    assert restored.stream is True


def test_llm_request_optional_fields() -> None:
    req = LlmRequest(
        **_ENV,
        request_id="req-2",
        messages=_MESSAGES,
        model="mistral-7b",
        system_prompt="You are helpful.",
        max_tokens=256,
        temperature=0.7,
        stream=False,
    )
    data = req.model_dump_json(exclude_none=True)
    assert '"mistral-7b"' in data
    assert '"stream":false' in data


def test_llm_request_invalid_request_id() -> None:
    with pytest.raises(ValidationError):
        LlmRequest(**_ENV, request_id="bad id!", messages=_MESSAGES)


def test_llm_response_ok_requires_text() -> None:
    with pytest.raises(ValidationError, match="must carry text"):
        LlmResponse(**_ENV, ok=True, request_id="r1", text=None)


def test_llm_response_ok_roundtrip() -> None:
    resp = LlmResponse(**_ENV, ok=True, request_id="r1", text="hello", duration_ms=42)
    restored = LlmResponse.model_validate_json(resp.model_dump_json())
    assert restored.ok is True
    assert restored.text == "hello"
    assert restored.duration_ms == 42


def test_llm_response_error_roundtrip() -> None:
    resp = LlmResponse(**_ENV, ok=False, request_id="r1", error="model_unavailable")
    restored = LlmResponse.model_validate_json(resp.model_dump_json())
    assert restored.ok is False
    assert restored.error == "model_unavailable"
    assert restored.text is None


def test_llm_chunk_event_roundtrip() -> None:
    chunk = LlmChunkEvent(**_ENV, request_id="r1", delta=" world", done=False)
    restored = LlmChunkEvent.model_validate_json(chunk.model_dump_json())
    assert restored.delta == " world"
    assert restored.done is False


def test_llm_chunk_event_terminal() -> None:
    chunk = LlmChunkEvent(**_ENV, request_id="r1", done=True, duration_ms=1234)
    assert chunk.delta is None
    assert chunk.duration_ms == 1234


def test_llm_chunk_event_error() -> None:
    chunk = LlmChunkEvent(
        **_ENV, request_id="r1", done=True, is_error=True, error="timeout"
    )
    assert chunk.is_error is True
    assert chunk.error == "timeout"


def test_extra_fields_ignored() -> None:
    req = LlmRequest.model_validate(
        {**_ENV, "request_id": "r1", "messages": _MESSAGES, "unknown_field": "x"}
    )
    assert not hasattr(req, "unknown_field")
