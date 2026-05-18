"""TTS protocol and types for NATS-based voice synthesis."""

from __future__ import annotations

import logging

# All canonical types now live in core/ports/tts; re-exported here for backward compat.
from lyra.core.ports.tts import SynthesisResult, TtsProtocol, TtsUnavailableError
from lyra.tts.engine_selector import (
    LANG_ISO_TO_QWEN,
    TTSConfig,
    build_generate_kwargs,
    load_tts_config,
    normalize_language,
)
from lyra.tts.text_normalization import normalize_text_for_tts

log = logging.getLogger(__name__)


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
