"""Tests for response builder helpers."""

import json

import pytest

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import build_stt_response, build_tts_response

# === STT Builder Tests ===


def test_build_stt_response_success():
    """STT builder returns valid JSON with all envelope fields for success case."""
    payload = {"request_id": "req-123", "trace_id": "trace-abc"}

    json_str = build_stt_response(
        payload,
        ok=True,
        text="Hello world",
        language="en",
        duration_seconds=2.5,
    )

    # Parse and verify
    import json

    data = json.loads(json_str)

    # Envelope fields
    assert data["contract_version"] == CONTRACT_VERSION
    assert data["trace_id"] == "trace-abc"
    assert "issued_at" in data

    # Response fields
    assert data["ok"] is True
    assert data["request_id"] == "req-123"
    assert data["text"] == "Hello world"
    assert data["language"] == "en"
    assert data["duration_seconds"] == 2.5
    assert data.get("error") is None


def test_build_stt_response_error():
    """STT builder returns valid JSON for error case."""
    payload = {"request_id": "req-456"}

    json_str = build_stt_response(
        payload,
        ok=False,
        error="Transcription failed",
    )

    import json

    data = json.loads(json_str)

    assert data["ok"] is False
    assert data["error"] == "Transcription failed"
    assert data.get("text") is None


def test_build_tts_response_success():
    """TTS builder returns valid JSON with all envelope fields for success case."""
    payload = {"request_id": "req-789"}

    json_str = build_tts_response(
        payload,
        ok=True,
        audio_b64="base64encodedaudio",
        mime_type="audio/wav",
        duration_ms=1500,
    )

    import json

    data = json.loads(json_str)

    # Envelope fields
    assert data["contract_version"] == CONTRACT_VERSION
    assert data["trace_id"] == "req-789"  # Falls back to request_id
    assert "issued_at" in data

    # Response fields
    assert data["ok"] is True
    assert data["request_id"] == "req-789"
    assert data["audio_b64"] == "base64encodedaudio"
    assert data["mime_type"] == "audio/wav"
    assert data["duration_ms"] == 1500
    assert data.get("error") is None


def test_build_tts_response_error():
    """TTS builder returns valid JSON for error case."""
    payload = {"request_id": "req-error"}

    json_str = build_tts_response(
        payload,
        ok=False,
        error="Synthesis failed",
    )

    import json

    data = json.loads(json_str)

    assert data["ok"] is False
    assert data["error"] == "Synthesis failed"
    assert data.get("audio_b64") is None


def test_trace_id_fallback():
    """trace_id falls back to request_id when not in payload."""
    payload = {"request_id": "req-fallback"}  # No trace_id

    json_str = build_stt_response(
        payload,
        ok=True,
        text="test",
        language="en",
        duration_seconds=1.0,
    )

    import json

    data = json.loads(json_str)

    assert data["trace_id"] == "req-fallback"


def test_trace_id_preserved_when_present():
    """Explicit trace_id is preserved when present in payload."""
    payload = {"request_id": "req-123", "trace_id": "explicit-trace"}

    json_str = build_stt_response(
        payload,
        ok=True,
        text="test",
        language="en",
        duration_seconds=1.0,
    )

    import json

    data = json.loads(json_str)

    assert data["trace_id"] == "explicit-trace"


def test_missing_request_id_raises_keyerror():
    """Missing request_id raises KeyError for STT builder."""
    payload = {"trace_id": "no-request-id"}

    with pytest.raises(KeyError):
        build_stt_response(
            payload,
            ok=True,
            text="test",
            language="en",
            duration_seconds=1.0,
        )


def test_missing_request_id_raises_keyerror_tts():
    """Missing request_id raises KeyError for TTS builder."""
    payload = {"trace_id": "no-request-id"}

    with pytest.raises(KeyError):
        build_tts_response(
            payload,
            ok=True,
            audio_b64="test",
            mime_type="audio/wav",
            duration_ms=1000,
        )


def test_build_tts_response_success_with_waveform():
    """TTS builder passes through optional waveform_b64 field."""
    payload = {"request_id": "req-wave"}

    json_str = build_tts_response(
        payload,
        ok=True,
        audio_b64="base64encodedaudio",
        mime_type="audio/wav",
        duration_ms=1500,
        waveform_b64="base64encodedwaveform",
    )

    data = json.loads(json_str)

    assert data["ok"] is True
    assert data["waveform_b64"] == "base64encodedwaveform"
