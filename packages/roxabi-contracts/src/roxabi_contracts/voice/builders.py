"""Response builder helpers for NATS workers.

Provides fail-safe construction of SttResponse and TtsResponse with
auto-populated envelope fields (contract_version, trace_id, issued_at).
"""

from __future__ import annotations

from datetime import datetime, timezone

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice.models import SttResponse, TtsResponse

__all__ = ["build_stt_response", "build_tts_response"]


def build_stt_response(  # noqa: PLR0913
    payload: dict,
    *,
    ok: bool,
    text: str | None = None,
    language: str | None = None,
    duration_seconds: float | None = None,
    error: str | None = None,
) -> str:
    """Build a valid SttResponse JSON string with auto-populated envelope fields.

    Args:
        payload: Request payload dict (must contain 'request_id')
        ok: Success flag
        text: Transcribed text (required when ok=True)
        language: Detected language (required when ok=True)
        duration_seconds: Audio duration (required when ok=True)
        error: Error message (set when ok=False)

    Returns:
        JSON string of SttResponse

    Raises:
        KeyError: If 'request_id' is missing from payload
    """
    request_id = payload["request_id"]
    trace_id = payload.get("trace_id") or request_id
    issued_at = datetime.now(timezone.utc)

    response = SttResponse(
        contract_version=CONTRACT_VERSION,
        trace_id=trace_id,
        issued_at=issued_at,
        ok=ok,
        request_id=request_id,
        text=text,
        language=language,
        duration_seconds=duration_seconds,
        error=error,
    )
    return response.model_dump_json()


def build_tts_response(  # noqa: PLR0913
    payload: dict,
    *,
    ok: bool,
    audio_b64: str | None = None,
    mime_type: str | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    waveform_b64: str | None = None,
) -> str:
    """Build a valid TtsResponse JSON string with auto-populated envelope fields.

    Args:
        payload: Request payload dict (must contain 'request_id')
        ok: Success flag
        audio_b64: Base64-encoded audio (required when ok=True)
        mime_type: Audio MIME type (required when ok=True)
        duration_ms: Audio duration in ms (required when ok=True)
        error: Error message (set when ok=False)
        waveform_b64: Optional waveform visualization

    Returns:
        JSON string of TtsResponse

    Raises:
        KeyError: If 'request_id' is missing from payload
    """
    request_id = payload["request_id"]
    trace_id = payload.get("trace_id") or request_id
    issued_at = datetime.now(timezone.utc)

    response = TtsResponse(
        contract_version=CONTRACT_VERSION,
        trace_id=trace_id,
        issued_at=issued_at,
        ok=ok,
        request_id=request_id,
        audio_b64=audio_b64,
        mime_type=mime_type,
        duration_ms=duration_ms,
        error=error,
        waveform_b64=waveform_b64,
    )
    return response.model_dump_json()
