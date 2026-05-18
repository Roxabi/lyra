"""STT protocol and types for NATS-based voice transcription."""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel

# All canonical types now live in core/ports/stt; re-exported here for backward compat.
from lyra.core.ports.stt import (
    _WHISPER_NOISE_TOKENS,
    STTNoiseError,
    STTProtocol,
    STTUnavailableError,
    TranscriptionResult,
    is_whisper_noise,
    mime_from_suffix,
)

log = logging.getLogger(__name__)

WHISPER_NOISE_TOKENS = _WHISPER_NOISE_TOKENS

__all__ = [
    "STTProtocol",
    "STTUnavailableError",
    "STTNoiseError",
    "TranscriptionResult",
    "STTConfig",
    "load_stt_config",
    "is_whisper_noise",
    "mime_from_suffix",
]


class STTConfig(BaseModel):
    model_size: str
    language_detection_threshold: float | None = None
    language_detection_segments: int | None = None
    language_fallback: str | None = None


def load_stt_config() -> STTConfig:
    model_size = os.environ.get("LYRA_STT_MODEL") or "large-v3-turbo"
    return STTConfig(model_size=model_size)
