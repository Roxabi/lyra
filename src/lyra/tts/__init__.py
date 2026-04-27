"""TTS protocol and types for NATS-based voice synthesis."""

from __future__ import annotations

import logging
from dataclasses import dataclass

# TtsProtocol now lives in core/ports/; re-exported here for backward compat.
from lyra.core.ports.tts import TtsProtocol
from lyra.tts.engine_selector import (
    LANG_ISO_TO_QWEN,
    TTSConfig,
    build_generate_kwargs,
    load_tts_config,
    normalize_language,
)
from lyra.tts.text_normalization import normalize_text_for_tts

log = logging.getLogger(__name__)


class TtsUnavailableError(Exception):
    """Raised when the TTS NATS adapter is unreachable (timeout or connection error)."""


@dataclass
class SynthesisResult:
    audio_bytes: bytes
    mime_type: str
    duration_ms: int | None  # None if WAV header unreadable
    waveform_b64: str | None = None  # 256-byte amplitude array, base64


__all__ = [
    "TtsProtocol",
    "TtsUnavailableError",
    "SynthesisResult",
    "TTSConfig",
    "load_tts_config",
    "LANG_ISO_TO_QWEN",
    "normalize_language",
    "build_generate_kwargs",
    "normalize_text_for_tts",
]
