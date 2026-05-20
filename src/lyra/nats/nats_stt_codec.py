"""SttCodec — pure encode/decode boundary between SttClient and transport bytes.

No I/O, no network, no NATS imports. encode replays NatsSttClient.transcribe()
payload-builder. decode handles Result[bytes, SanitizedError] → TranscriptionResult.

CB is NOT touched on decode failure — see spec § "Error path — decode failure".
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from lyra.core.ports.stt import TranscriptionResult
from lyra.transport._result import Err, Ok, Result, SanitizedError
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SttRequest, SttResponse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SttEncodeParams:
    """Optional parameters for SttCodec.encode(); mirrors NatsSttClient init args."""

    model: str = field(default="large-v3-turbo")
    language_detection_threshold: float | None = field(default=None)
    language_detection_segments: int | None = field(default=None)
    language_fallback: str | None = field(default=None)


class SttCodec:
    """Pure encode/decode for the STT domain.

    encode: builds SttRequest bytes from caller args (mirrors NatsSttClient.transcribe).
    decode: maps Result[bytes, SanitizedError] → TranscriptionResult; never raises.
    """

    def encode(self, audio: bytes, mime: str, params: SttEncodeParams) -> bytes:
        """Build canonical SttRequest payload bytes.

        Mirrors NatsSttClient.transcribe() payload-builder exactly so the wire
        format is bit-for-bit identical.
        """
        request = SttRequest(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            request_id=str(uuid4()),
            audio_b64=base64.b64encode(audio).decode("ascii"),
            mime_type=mime,
            model=params.model,
            language_detection_threshold=params.language_detection_threshold,
            language_detection_segments=params.language_detection_segments,
            language_fallback=params.language_fallback,
        )
        return request.model_dump_json(exclude_none=True).encode("utf-8")

    def decode(self, result: Result[bytes, SanitizedError]) -> TranscriptionResult:
        """Map transport Result → TranscriptionResult; CB unaffected on failure."""
        if isinstance(result, Err):
            err = result.error
            return TranscriptionResult(
                text="",
                language="",
                duration_seconds=0.0,
                error=err.code,
            )
        assert isinstance(result, Ok)
        try:
            resp = SttResponse.model_validate_json(result.value)
        except (ValidationError, ValueError) as exc:
            log.warning("SttCodec.decode: validation error: %r", exc)
            return TranscriptionResult(
                text="",
                language="",
                duration_seconds=0.0,
                error="decode.validation_error",
            )
        if not resp.ok:
            return TranscriptionResult(
                text="",
                language="",
                duration_seconds=0.0,
                error=resp.error or "stt.worker_error",
            )
        return TranscriptionResult(
            text=resp.text,  # type: ignore[arg-type]
            language=resp.language,  # type: ignore[arg-type]
            duration_seconds=resp.duration_seconds,  # type: ignore[arg-type]
        )
