"""Response builders for voice-domain NATS contracts.

Convenient factory functions that handle common envelope fields
(contract_version, trace_id, issued_at) and return JSON-serialized
response models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice.models import SttResponse, TtsResponse

__all__ = ["build_stt_response", "build_tts_response"]


def build_stt_response(  # noqa: PLR0913 — builder with optional success/error fields
    payload: dict[str, Any],
    *,
    ok: bool,
    text: str | None = None,
    language: str | None = None,
    duration_seconds: float | None = None,
    error: str | None = None,
    issued_at: datetime | None = None,
) -> str:
    """Build an SttResponse JSON string from a request payload.

    Args:
        payload: The request payload dict. Must contain 'request_id'.
            'trace_id' is optional; falls back to request_id.
        ok: Success flag. When True, text, language, and duration_seconds
            are required (validated by SttResponse model).
        text: Transcribed text (required when ok=True).
        language: Detected language code (required when ok=True).
        duration_seconds: Audio duration in seconds (required when ok=True).
        error: Error message (required when ok=False).
        issued_at: Timestamp to embed in the response envelope. Defaults to
            ``datetime.now(timezone.utc)``. Pass a fixed value in tests to
            avoid mocking the clock.

    Returns:
        JSON string of the SttResponse model.

    Raises:
        KeyError: If 'request_id' is missing from payload.
    """
    request_id = payload["request_id"]
    trace_id = payload.get("trace_id") or request_id

    response = SttResponse(
        contract_version=CONTRACT_VERSION,
        trace_id=trace_id,
        issued_at=issued_at if issued_at is not None else datetime.now(timezone.utc),
        ok=ok,
        request_id=request_id,
        text=text,
        language=language,
        duration_seconds=duration_seconds,
        error=error,
    )
    return response.model_dump_json()


def build_tts_response(  # noqa: PLR0913 — builder with optional success/error fields
    payload: dict[str, Any],
    *,
    ok: bool,
    audio_b64: str | None = None,
    mime_type: str | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    waveform_b64: str | None = None,
    issued_at: datetime | None = None,
) -> str:
    """Build a TtsResponse JSON string from a request payload.

    Args:
        payload: The request payload dict. Must contain 'request_id'.
            'trace_id' is optional; falls back to request_id.
        ok: Success flag. When True, audio_b64, mime_type, and duration_ms
            are required (validated by TtsResponse model).
        audio_b64: Base64-encoded audio data (required when ok=True).
        mime_type: Audio MIME type (required when ok=True).
        duration_ms: Audio duration in milliseconds (required when ok=True).
        error: Error message (required when ok=False).
        waveform_b64: Optional base64-encoded waveform visualization.
        issued_at: Timestamp to embed in the response envelope. Defaults to
            ``datetime.now(timezone.utc)``. Pass a fixed value in tests to
            avoid mocking the clock.

    Returns:
        JSON string of the TtsResponse model.

    Raises:
        KeyError: If 'request_id' is missing from payload.
    """
    request_id = payload["request_id"]
    trace_id = payload.get("trace_id") or request_id

    response = TtsResponse(
        contract_version=CONTRACT_VERSION,
        trace_id=trace_id,
        issued_at=issued_at if issued_at is not None else datetime.now(timezone.utc),
        ok=ok,
        request_id=request_id,
        audio_b64=audio_b64,
        mime_type=mime_type,
        duration_ms=duration_ms,
        error=error,
        waveform_b64=waveform_b64,
    )
    return response.model_dump_json()
