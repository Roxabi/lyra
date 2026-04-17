"""Roundtrip + success-path invariant tests for roxabi_contracts.voice models.

Parametrized over all four envelope subclasses (TtsRequest/TtsResponse/
SttRequest/SttResponse). Invariants per ADR-044 and spec #763 drift
items 1, 3, 4.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import BaseModel

from roxabi_contracts.voice import (
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from roxabi_contracts.voice.fixtures import sample_transcript_en, silence_wav_16khz

_ENVELOPE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-trace",
    "issued_at": datetime(2026, 4, 17, tzinfo=timezone.utc),
}


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            TtsRequest,
            {**_ENVELOPE, "request_id": "r1", "text": sample_transcript_en},
        ),
        (
            TtsResponse,
            {
                **_ENVELOPE,
                "ok": True,
                "request_id": "r1",
                "audio_b64": _b64(silence_wav_16khz),
                "mime_type": "audio/wav",
                "duration_ms": 1000,
            },
        ),
        (
            SttRequest,
            {
                **_ENVELOPE,
                "request_id": "r2",
                "audio_b64": _b64(silence_wav_16khz),
                "model": "large-v3-turbo",
            },
        ),
        (
            SttResponse,
            {
                **_ENVELOPE,
                "ok": True,
                "request_id": "r2",
                "text": sample_transcript_en,
                "language": "en",
                "duration_seconds": 1.0,
            },
        ),
    ],
    ids=["TtsRequest", "TtsResponse", "SttRequest", "SttResponse"],
)
def test_roundtrip(model: type[BaseModel], payload: dict[str, Any]) -> None:
    """model.model_validate(payload) → dump_json → model_validate_json → equal."""
    inst = model.model_validate(payload)
    parsed = model.model_validate_json(inst.model_dump_json())
    assert parsed == inst


def test_tts_response_success_invariant() -> None:
    """Drift #1: ok=True implies audio_b64 + mime_type + duration_ms non-null."""
    resp = TtsResponse(
        **_ENVELOPE,
        ok=True,
        request_id="r1",
        audio_b64=_b64(silence_wav_16khz),
        mime_type="audio/wav",
        duration_ms=1000,
    )
    assert resp.audio_b64 is not None
    assert resp.mime_type is not None
    assert resp.duration_ms is not None


def test_stt_response_success_invariant() -> None:
    """Drift #3+#4: ok=True implies text + language + duration_seconds non-null."""
    resp = SttResponse(
        **_ENVELOPE,
        ok=True,
        request_id="r2",
        text="hello",
        language="en",
        duration_seconds=1.0,
    )
    assert resp.text is not None
    assert resp.language is not None
    assert resp.duration_seconds is not None


def test_tts_response_error_path_allows_null_mime_type() -> None:
    """Error path: mime_type is legitimately absent when ok=False."""
    resp = TtsResponse(
        **_ENVELOPE,
        ok=False,
        request_id="r1",
        error="engine_unavailable",
    )
    assert resp.mime_type is None
    assert resp.audio_b64 is None
