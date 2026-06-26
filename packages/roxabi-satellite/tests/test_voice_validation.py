"""Voice ingress validation tests."""

from __future__ import annotations

from datetime import datetime, timezone

from roxabi_satellite.voice.validation import validate_stt_request, validate_tts_request

_NOW = datetime.now(timezone.utc).isoformat()


def _blob_ref(**overrides: object) -> dict:
    base = {
        "store_key": "sha256:" + "ab" * 32,
        "mime": "audio/wav",
        "size": 1024,
        "source": "test",
        "content_hash": "deadbeef",
        "created_at": _NOW,
    }
    base.update(overrides)
    return base


def test_stt_missing_request_id() -> None:
    result = validate_stt_request({"blob_ref": _blob_ref()}, default_model="large-v3-turbo")
    assert result.error_code == "malformed_request"


def test_stt_bool_segments_rejected() -> None:
    payload = {
        "request_id": "req-1",
        "blob_ref": _blob_ref(),
        "language_detection_segments": True,
    }
    result = validate_stt_request(payload, default_model="large-v3-turbo")
    assert result.error_code == "malformed_request"


def test_tts_engine_unavailable() -> None:
    payload = {"request_id": "req-1", "text": "hello", "engine": "missing"}
    result = validate_tts_request(
        payload,
        default_engine="qwen",
        engine_available=lambda e: e == "qwen",
    )
    assert result.error_code == "engine_unavailable"


def test_stt_happy_path() -> None:
    payload = {"request_id": "req-1", "blob_ref": _blob_ref()}
    result = validate_stt_request(payload, default_model="large-v3-turbo")
    assert result.error_code is None
    assert result.storage_blob_ref is not None