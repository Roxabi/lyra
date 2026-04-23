"""Unit tests for voice-domain response builders.

Tests for build_stt_response and build_tts_response factory functions
that handle common envelope fields and return JSON-serialized responses.
"""

from __future__ import annotations

import base64
import json

import pytest

from roxabi_contracts.voice.builders import build_stt_response, build_tts_response
from roxabi_contracts.voice.fixtures import sample_transcript_en, silence_wav_16khz


def _b64(b: bytes) -> str:
    """Encode bytes to base64 string."""
    return base64.b64encode(b).decode("ascii")


class TestBuildSttResponse:
    """Tests for build_stt_response builder function."""

    def test_build_stt_response_success(self) -> None:
        """ok=True with text, language, duration_seconds builds valid JSON."""
        # Arrange
        payload = {"request_id": "stt-req-001"}

        # Act
        json_str = build_stt_response(
            payload,
            ok=True,
            text=sample_transcript_en,
            language="en",
            duration_seconds=1.0,
        )

        # Assert
        data = json.loads(json_str)
        assert data["ok"] is True
        assert data["request_id"] == "stt-req-001"
        assert data["text"] == sample_transcript_en
        assert data["language"] == "en"
        assert data["duration_seconds"] == 1.0
        assert data["error"] is None
        assert data["contract_version"] == "1"
        assert "trace_id" in data
        assert "issued_at" in data

    def test_build_stt_response_error(self) -> None:
        """ok=False with error message builds valid JSON."""
        # Arrange
        payload = {"request_id": "stt-req-002"}

        # Act
        json_str = build_stt_response(
            payload,
            ok=False,
            error="audio_decode_failed",
        )

        # Assert
        data = json.loads(json_str)
        assert data["ok"] is False
        assert data["request_id"] == "stt-req-002"
        assert data["error"] == "audio_decode_failed"
        assert data["text"] is None
        assert data["language"] is None
        assert data["duration_seconds"] is None


class TestBuildTtsResponse:
    """Tests for build_tts_response builder function."""

    def test_build_tts_response_success(self) -> None:
        """ok=True with audio_b64, mime_type, duration_ms builds valid JSON."""
        # Arrange
        payload = {"request_id": "tts-req-001"}
        audio_b64 = _b64(silence_wav_16khz)

        # Act
        json_str = build_tts_response(
            payload,
            ok=True,
            audio_b64=audio_b64,
            mime_type="audio/wav",
            duration_ms=1000,
        )

        # Assert
        data = json.loads(json_str)
        assert data["ok"] is True
        assert data["request_id"] == "tts-req-001"
        assert data["audio_b64"] == audio_b64
        assert data["mime_type"] == "audio/wav"
        assert data["duration_ms"] == 1000
        assert data["error"] is None
        assert data["contract_version"] == "1"
        assert "trace_id" in data
        assert "issued_at" in data

    def test_build_tts_response_error(self) -> None:
        """ok=False with error message builds valid JSON."""
        # Arrange
        payload = {"request_id": "tts-req-002"}

        # Act
        json_str = build_tts_response(
            payload,
            ok=False,
            error="engine_unavailable",
        )

        # Assert
        data = json.loads(json_str)
        assert data["ok"] is False
        assert data["request_id"] == "tts-req-002"
        assert data["error"] == "engine_unavailable"
        assert data["audio_b64"] is None
        assert data["mime_type"] is None
        assert data["duration_ms"] is None


class TestTraceIdFallback:
    """Tests for trace_id fallback behavior."""

    def test_trace_id_fallback(self) -> None:
        """trace_id missing falls back to request_id."""
        # Arrange
        payload = {"request_id": "fallback-test-001"}

        # Act
        json_str = build_stt_response(
            payload,
            ok=False,
            error="test_error",
        )

        # Assert
        data = json.loads(json_str)
        assert data["trace_id"] == "fallback-test-001"
        assert data["request_id"] == "fallback-test-001"

    def test_trace_id_preserved_when_present(self) -> None:
        """trace_id is preserved when explicitly provided."""
        # Arrange
        payload = {
            "request_id": "req-001",
            "trace_id": "explicit-trace-123",
        }

        # Act
        json_str = build_tts_response(
            payload,
            ok=False,
            error="test_error",
        )

        # Assert
        data = json.loads(json_str)
        assert data["trace_id"] == "explicit-trace-123"
        assert data["request_id"] == "req-001"


class TestMissingRequestId:
    """Tests for missing request_id validation."""

    def test_missing_request_id_raises_keyerror(self) -> None:
        """payload without request_id raises KeyError."""
        # Arrange
        payload: dict[str, str] = {}

        # Act & Assert
        with pytest.raises(KeyError, match="request_id"):
            build_stt_response(payload, ok=False, error="test_error")

    def test_missing_request_id_raises_keyerror_tts(self) -> None:
        """payload without request_id raises KeyError for TTS builder."""
        # Arrange
        payload: dict[str, str] = {}

        # Act & Assert
        with pytest.raises(KeyError, match="request_id"):
            build_tts_response(payload, ok=False, error="test_error")
