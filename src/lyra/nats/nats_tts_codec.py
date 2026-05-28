"""TtsCodec — pure encode/decode boundary between TtsClient and transport bytes.

No I/O, no network, no NATS imports. encode replays NatsTtsClient.synthesize()
payload-builder. decode handles Result[bytes, SanitizedError] → SynthesisResult.

CB is NOT touched on decode failure — see spec § "Error path — decode failure".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import ValidationError

from lyra.core.ports.tts import SynthesisResult
from lyra.transport._result import Err, Result, SanitizedError
from roxabi_contracts import BlobRef
from roxabi_contracts.blob_ref import PENDING_STORE_KEY
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import TtsRequest, TtsResponse
from roxabi_contracts.voice.constants import TTS_CONFIG_FIELDS

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import AgentTTSConfig

log = logging.getLogger(__name__)

# Sentinel BlobRef for error paths where no real blob was produced.
_SENTINEL_BLOB_REF = BlobRef(
    store_key=PENDING_STORE_KEY,
    content_hash="",
    mime="",
    size=0,
    source="",
)


class TtsCodec:
    """Pure encode/decode for the TTS domain.

    encode: builds TtsRequest bytes from caller args (mirrors NatsTtsClient.synthesize).
    decode: maps Result[bytes, SanitizedError] → SynthesisResult; never raises.
    """

    def encode(
        self,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> bytes:
        """Build canonical TtsRequest payload bytes.

        Mirrors NatsTtsClient.synthesize() payload-builder exactly so the wire
        format is bit-for-bit identical.
        """
        req_kwargs: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "trace_id": str(uuid4()),
            "issued_at": datetime.now(timezone.utc),
            "request_id": str(uuid4()),
            "text": text,
            "language": language,
            "voice": voice,
            "fallback_language": fallback_language,
            "chunked": True,
        }
        if agent_tts is not None:
            for field in TTS_CONFIG_FIELDS:
                val = getattr(agent_tts, field, None)
                if val is not None:
                    req_kwargs[field] = val
            if language is None and getattr(agent_tts, "language", None) is not None:
                req_kwargs["language"] = agent_tts.language
            if voice is None and getattr(agent_tts, "voice", None) is not None:
                req_kwargs["voice"] = agent_tts.voice
        request = TtsRequest.model_validate(req_kwargs)
        return request.model_dump_json(exclude_none=True).encode("utf-8")

    def decode(self, result: Result[bytes, SanitizedError]) -> SynthesisResult:
        """Map transport Result → SynthesisResult; CB NOT touched on decode failure."""
        if isinstance(result, Err):
            err = result.error
            return SynthesisResult(
                blob_ref=_SENTINEL_BLOB_REF,
                mime_type="",
                duration_ms=None,
                error=err.code,
                error_message=err.message,
                retryable=err.retryable,
                unavailable=True,
            )
        try:
            resp = TtsResponse.model_validate_json(result.value)
        except (ValidationError, ValueError) as exc:
            log.warning("TtsCodec.decode: validation error: %r", exc)
            return SynthesisResult(
                blob_ref=_SENTINEL_BLOB_REF,
                mime_type="",
                duration_ms=None,
                error="decode.validation_error",
                error_message="response payload failed validation",
                retryable=False,
                unavailable=False,
            )
        if not resp.ok:
            if resp.worker_error is not None:
                we = resp.worker_error
                return SynthesisResult(
                    blob_ref=_SENTINEL_BLOB_REF,
                    mime_type="",
                    duration_ms=None,
                    error=we.code,
                    error_message=we.message,
                    error_detail=we.detail,
                    retryable=we.retryable,
                    unavailable=False,
                )
            # Older worker: fall back to flat resp.error
            flat_error = resp.error or "tts.worker_error"
            return SynthesisResult(
                blob_ref=_SENTINEL_BLOB_REF,
                mime_type="",
                duration_ms=None,
                error=flat_error,
                error_message=flat_error,
                retryable=False,
                unavailable=False,
            )
        return SynthesisResult(
            blob_ref=resp.blob_ref,  # type: ignore[arg-type]
            mime_type=resp.mime_type,  # type: ignore[arg-type]
            duration_ms=resp.duration_ms,  # type: ignore[arg-type]
            waveform_b64=resp.waveform_b64,
        )
