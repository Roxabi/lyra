"""STT/TTS ingress validation against ``roxabi_contracts.voice`` models."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from pydantic import ValidationError

from roxabi_blobs import BlobRef as StorageBlobRef
from roxabi_contracts.voice.models import SttRequest, TtsRequest
from roxabi_satellite.envelope import coerce_envelope_fields
from roxabi_satellite.tokens import validate_nats_token

log = logging.getLogger(__name__)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class TtsValidationOutcome:
    error_code: str | None
    cleaned_text: str | None = None
    engine: str | None = None
    request: TtsRequest | None = None


@dataclass(frozen=True)
class SttValidationOutcome:
    error_code: str | None
    overrides: dict | None = None
    request: SttRequest | None = None
    storage_blob_ref: StorageBlobRef | None = None


def _stt_raw_payload_types_valid(payload: dict) -> bool:
    language = payload.get("language")
    if language is not None and not isinstance(language, str):
        return False

    threshold = payload.get("language_detection_threshold")
    if threshold is not None and (
        isinstance(threshold, bool) or not isinstance(threshold, (int, float))
    ):
        return False

    segments = payload.get("language_detection_segments")
    if segments is not None and (isinstance(segments, bool) or not isinstance(segments, int)):  # noqa: E501
        return False

    fallback = payload.get("language_fallback")
    if fallback is not None and not isinstance(fallback, str):
        return False

    prompt = payload.get("initial_prompt")
    if prompt is not None and not isinstance(prompt, str):
        return False

    task = payload.get("task")
    if task is not None and (not isinstance(task, str) or task not in ("transcribe", "translate")):  # noqa: E501
        return False

    raw_ref = payload.get("blob_ref")
    if raw_ref is not None and not isinstance(raw_ref, dict):
        return False

    request_id = payload.get("request_id", "")
    if not isinstance(request_id, str):
        return False

    return True


def _tts_raw_payload_types_valid(payload: dict) -> bool:
    text = payload.get("text")
    if not isinstance(text, str):
        return False

    request_id = payload.get("request_id", "")
    if not isinstance(request_id, str):
        return False

    engine = payload.get("engine")
    if engine is not None and not isinstance(engine, str):
        return False

    chunked = payload.get("chunked")
    if chunked is not None and not isinstance(chunked, bool):
        return False

    chunk_size = payload.get("chunk_size")
    if chunk_size is not None and (isinstance(chunk_size, bool) or not isinstance(chunk_size, int)):  # noqa: E501
        return False

    for field in ("exaggeration", "cfg_weight", "segment_gap", "crossfade"):
        val = payload.get(field)
        if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float))):  # noqa: E501
            return False

    for field in (
        "language",
        "voice",
        "speed",
        "accent",
        "personality",
        "emotion",
        "fallback_language",
    ):
        val = payload.get(field)
        if val is not None and not isinstance(val, str):
            return False

    return True


def _stt_overrides(req: SttRequest) -> dict:
    overrides: dict = {}
    if req.language is not None:
        overrides["language"] = req.language
    if req.language_detection_threshold is not None:
        overrides["language_detection_threshold"] = req.language_detection_threshold
    if req.language_detection_segments is not None:
        overrides["language_detection_segments"] = req.language_detection_segments
    if req.language_fallback is not None:
        overrides["language_fallback"] = req.language_fallback
    if req.initial_prompt is not None:
        overrides["initial_prompt"] = req.initial_prompt
    if req.task is not None:
        overrides["task"] = req.task
    return overrides


_MALFORMED = TtsValidationOutcome(error_code="malformed_request")
_STT_MALFORMED = SttValidationOutcome(error_code="malformed_request")


def validate_tts_request(
    payload: dict,
    *,
    default_engine: str,
    engine_available: Callable[[str], bool],
) -> TtsValidationOutcome:
    if not _tts_raw_payload_types_valid(payload):
        return _MALFORMED

    try:
        req = TtsRequest.model_validate(coerce_envelope_fields(payload))
    except (ValidationError, TypeError):
        return _MALFORMED

    if not _REQUEST_ID_RE.match(req.request_id):
        return _MALFORMED

    text = req.text
    _newline_count = text.count("\n") + text.count("\r")
    if _newline_count:
        text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        log.debug(
            "text_newlines_stripped",
            extra={"request_id": req.request_id, "removed": _newline_count},
        )

    if not text.strip():
        log.warning(
            "text_empty_after_strip",
            extra={"request_id": req.request_id, "original_length": len(req.text)},
        )
        return _MALFORMED

    engine = req.engine or default_engine
    try:
        validate_nats_token(engine, kind="engine")
    except ValueError:
        return _MALFORMED

    if not engine_available(engine):
        return TtsValidationOutcome(error_code="engine_unavailable")

    cleaned = req.model_copy(update={"text": text})
    return TtsValidationOutcome(
        error_code=None, cleaned_text=text, engine=engine, request=cleaned
    )


def validate_stt_request(
    payload: dict,
    *,
    default_model: str,
) -> SttValidationOutcome:
    if not _stt_raw_payload_types_valid(payload):
        return _STT_MALFORMED

    try:
        data = coerce_envelope_fields(payload)
        if not data.get("model"):
            data["model"] = default_model
        req = SttRequest.model_validate(data)
    except (ValidationError, TypeError):
        return _STT_MALFORMED

    if not _REQUEST_ID_RE.match(req.request_id):
        return _STT_MALFORMED

    try:
        storage_ref = StorageBlobRef.model_validate(req.blob_ref.model_dump())
    except (ValidationError, TypeError):
        return _STT_MALFORMED

    return SttValidationOutcome(
        error_code=None,
        overrides=_stt_overrides(req),
        request=req,
        storage_blob_ref=storage_ref,
    )