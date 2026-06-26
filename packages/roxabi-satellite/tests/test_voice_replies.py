"""STT/TTS error reply wire format tests."""

from __future__ import annotations

import json

from roxabi_contracts.errors import WorkerError

from roxabi_satellite.voice.replies import build_stt_error_reply, build_tts_error_reply


def test_stt_error_includes_worker_error() -> None:
    we = WorkerError(code="malformed_request", message="bad", retryable=False)
    raw = build_stt_error_reply("trace-1", "req-1", we)
    data = json.loads(raw)
    assert data["ok"] is False
    assert data["worker_error"]["code"] == "malformed_request"


def test_tts_empty_request_id_uses_construct() -> None:
    we = WorkerError(code="malformed_request", message="bad", retryable=False)
    raw = build_tts_error_reply("trace-1", "", we)
    data = json.loads(raw)
    assert data["request_id"] == ""
    assert data["worker_error"]["code"] == "malformed_request"