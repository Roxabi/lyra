"""Roundtrip + success-path invariant tests for roxabi_contracts.voice models.

Parametrized over all four envelope subclasses (TtsRequest/TtsResponse/
SttRequest/SttResponse). Invariants per ADR-044 (absorbed into ADR-049)
and spec #763 drift
items 1, 3, 4.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from roxabi_contracts.blob_ref import BlobRef
from roxabi_contracts.voice import (
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from roxabi_contracts.voice.fixtures import sample_transcript_en

_ENVELOPE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-trace",
    "issued_at": datetime(2026, 4, 17, tzinfo=timezone.utc),
}


def _blob_ref_dict() -> dict[str, Any]:
    return {
        "store_key": "blob-tts-123",
        "content_hash": "deadbeef",
        "mime": "audio/wav",
        "size": 1024,
        "source": "voicecli",
    }


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
                "blob_ref": _blob_ref_dict(),
                "mime_type": "audio/wav",
                "duration_ms": 1000,
            },
        ),
        (
            SttRequest,
            {
                **_ENVELOPE,
                "request_id": "r2",
                "blob_ref": _blob_ref_dict(),
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


def test_tts_response_success_accepts_complete_payload() -> None:
    """Valid ok=True payload with all required success fields parses cleanly."""
    resp = TtsResponse(
        **_ENVELOPE,
        ok=True,
        request_id="r1",
        blob_ref=BlobRef(**_blob_ref_dict()),
        mime_type="audio/wav",
        duration_ms=1000,
    )
    assert resp.blob_ref is not None
    assert resp.mime_type is not None
    assert resp.duration_ms is not None


@pytest.mark.parametrize(
    "missing_field",
    ["blob_ref", "mime_type", "duration_ms"],
)
def test_tts_response_ok_true_rejects_missing_field(missing_field: str) -> None:
    """Drift #1: ok=True with any missing success field raises ValidationError."""
    payload: dict[str, Any] = {
        **_ENVELOPE,
        "ok": True,
        "request_id": "r1",
        "blob_ref": _blob_ref_dict(),
        "mime_type": "audio/wav",
        "duration_ms": 1000,
    }
    payload[missing_field] = None
    with pytest.raises(ValidationError, match="must carry"):
        TtsResponse.model_validate(payload)


def test_stt_response_success_accepts_complete_payload() -> None:
    """Valid ok=True payload with all required success fields parses cleanly."""
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


@pytest.mark.parametrize(
    "missing_field",
    ["text", "language", "duration_seconds"],
)
def test_stt_response_ok_true_rejects_missing_field(missing_field: str) -> None:
    """Drift #3+#4: ok=True with any missing success field raises ValidationError."""
    payload: dict[str, Any] = {
        **_ENVELOPE,
        "ok": True,
        "request_id": "r2",
        "text": "hello",
        "language": "en",
        "duration_seconds": 1.0,
    }
    payload[missing_field] = None
    with pytest.raises(ValidationError, match="must carry"):
        SttResponse.model_validate(payload)


def test_tts_response_error_path_allows_null_mime_type() -> None:
    """Error path: mime_type is legitimately absent when ok=False."""
    resp = TtsResponse(
        **_ENVELOPE,
        ok=False,
        request_id="r1",
        error="engine_unavailable",
    )
    assert resp.mime_type is None
    assert resp.blob_ref is None


def test_stt_response_error_path_allows_null_success_fields() -> None:
    """Error path: text/language/duration_seconds absent when ok=False."""
    resp = SttResponse(
        **_ENVELOPE,
        ok=False,
        request_id="r2",
        error="audio_decode_failed",
    )
    assert resp.text is None
    assert resp.language is None
    assert resp.duration_seconds is None


def test_stt_request_optional_prompt_and_task_round_trip() -> None:
    """initial_prompt + task survive validate → dump_json → validate_json."""
    req = SttRequest(
        **_ENVELOPE,
        request_id="r2",
        blob_ref=BlobRef(**_blob_ref_dict()),
        model="large-v3-turbo",
        initial_prompt="Bonjour. Voici un texte avec une ponctuation correcte.",
        task="transcribe",
    )
    parsed = SttRequest.model_validate_json(req.model_dump_json())
    assert parsed.initial_prompt == req.initial_prompt
    assert parsed.task == "transcribe"


def test_stt_request_omits_prompt_and_task_by_default() -> None:
    """Optional fields default to None — required-field round-trip stays valid."""
    req = SttRequest(
        **_ENVELOPE,
        request_id="r2",
        blob_ref=BlobRef(**_blob_ref_dict()),
        model="large-v3-turbo",
    )
    assert req.initial_prompt is None
    assert req.task is None


# ---------------------------------------------------------------------------
# Negative tests for `StringConstraints(min_length=1)` — pin the declared
# constraints so a silent weakening (e.g. dropping the Annotated wrapper)
# fails here. Covers drift-unrelated correctness guarantees on the base
# envelope (trace_id) and the per-domain request models (text, request_id).
# Note: blob_ref is a nested model, not a constrained string — excluded.
# ---------------------------------------------------------------------------

_MIN_LENGTH_CASES: list[tuple[type[BaseModel], str]] = [
    (TtsRequest, "text"),
    (TtsRequest, "request_id"),
    (TtsRequest, "trace_id"),
    (SttRequest, "request_id"),
    (SttRequest, "trace_id"),
    (TtsResponse, "request_id"),
    (TtsResponse, "trace_id"),
    (SttResponse, "request_id"),
    (SttResponse, "trace_id"),
]


def _valid_payload_for(model: type[BaseModel]) -> dict[str, Any]:
    if model is TtsRequest:
        return {**_ENVELOPE, "request_id": "r1", "text": sample_transcript_en}
    if model is SttRequest:
        return {
            **_ENVELOPE,
            "request_id": "r2",
            "blob_ref": _blob_ref_dict(),
            "model": "large-v3-turbo",
        }
    if model is TtsResponse:
        return {**_ENVELOPE, "ok": False, "request_id": "r1", "error": "x"}
    if model is SttResponse:
        return {**_ENVELOPE, "ok": False, "request_id": "r2", "error": "x"}
    raise AssertionError(f"Unknown model {model!r}")


@pytest.mark.parametrize(
    ("model", "field"),
    _MIN_LENGTH_CASES,
    ids=[f"{m.__name__}.{f}" for m, f in _MIN_LENGTH_CASES],
)
def test_min_length_one_rejects_empty_string(
    model: type[BaseModel], field: str
) -> None:
    """Empty string for any ``min_length=1`` field raises ValidationError."""
    payload = _valid_payload_for(model)
    payload[field] = ""
    with pytest.raises(ValidationError):
        model.model_validate(payload)


# ---------------------------------------------------------------------------
# Legacy-field regression guard — spec SC-2, plan T6.
# ContractEnvelope uses extra="ignore", so audio_b64 is silently dropped; the
# request then fails on the now-required blob_ref. The model rejects the
# legacy payload shape either way — this test pins that the failure happens.
# ---------------------------------------------------------------------------


def test_stt_request_rejects_legacy_audio_b64_field() -> None:
    """Spec SC-2: SttRequest(audio_b64=...) must raise ValidationError."""
    payload: dict[str, Any] = {
        **_ENVELOPE,
        "request_id": "r-legacy",
        "audio_b64": "ZmFrZS1ieXRlcw==",
        "model": "large-v3-turbo",
    }
    with pytest.raises(ValidationError):
        SttRequest.model_validate(payload)
