"""Wire-safe STT/TTS error reply builders with structured ``WorkerError``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.voice.models import SttResponse, TtsResponse
from roxabi_satellite.errors import VOICE_VALIDATION_ERRORS


def build_tts_error_reply(
    trace_id: str,
    request_id: str,
    worker_error: WorkerError,
    job_id: str | None = None,
) -> bytes:
    fields: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": trace_id,
        "issued_at": datetime.now(timezone.utc),
        "ok": False,
        "request_id": request_id or "",
        "error": worker_error.code,
        "worker_error": worker_error,
    }
    if job_id:
        fields["job_id"] = job_id
    m = (
        TtsResponse.model_construct(**fields)
        if not request_id
        else TtsResponse(**fields)
    )
    return m.model_dump_json(exclude_none=True).encode()


def build_stt_error_reply(
    trace_id: str,
    request_id: str,
    worker_error: WorkerError,
    job_id: str | None = None,
) -> bytes:
    extra: dict[str, str] = {}
    if job_id:
        extra["job_id"] = job_id
    fields: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": trace_id,
        "issued_at": datetime.now(timezone.utc),
        "ok": False,
        "request_id": request_id or "",
        "error": worker_error.code,
        "worker_error": worker_error,
        **extra,
    }
    m = SttResponse.model_construct(**fields) if not request_id else SttResponse(**fields)  # noqa: E501
    return m.model_dump_json(exclude_none=True).encode()


def voice_validation_error(code: str) -> WorkerError:
    return VOICE_VALIDATION_ERRORS.get(
        code,
        WorkerError(
            code=code,
            message=code.replace("_", " ").capitalize(),
            retryable=False,
        ),
    )