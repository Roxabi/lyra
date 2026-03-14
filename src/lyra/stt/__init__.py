"""STT service — thin wrapper around voiceCLI (faster-whisper + personal vocab)."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

WHISPER_NOISE_TOKENS = {"[music]", "[applause]", "[laughter]", "[silence]", "[noise]"}


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration_seconds: float


@dataclass
class STTConfig:
    model_size: str


def load_stt_config() -> STTConfig:
    model_size = os.environ.get("STT_MODEL_SIZE", "large-v3-turbo")
    return STTConfig(model_size=model_size)


def is_whisper_noise(text: str) -> bool:
    """Return True if the text is empty or a known Whisper noise token."""
    stripped = text.strip().lower()
    return not stripped or stripped in WHISPER_NOISE_TOKENS


class STTService:
    """Async STT service delegating to voiceCLI (faster-whisper + personal vocab)."""

    def __init__(self, config: STTConfig) -> None:
        self._model = config.model_size
        log.debug("STTService init: model=%s (via voiceCLI)", self._model)

    async def transcribe(self, path: Path | str) -> TranscriptionResult:
        return await asyncio.to_thread(self._transcribe_sync, str(path))

    def _transcribe_sync(self, path: str) -> TranscriptionResult:
        try:
            from voicecli.config import load_vocab, vocab_to_prompt
            from voicecli.transcribe import transcribe as _transcribe

            initial_prompt = vocab_to_prompt(load_vocab())
            vc_result = _transcribe(
                Path(path), model=self._model, initial_prompt=initial_prompt
            )

            duration = (
                max((seg["end"] for seg in vc_result.segments), default=0.0)
                if vc_result.segments
                else 0.0
            )
            result = TranscriptionResult(
                text=vc_result.text,
                language=vc_result.language or "unknown",
                duration_seconds=duration,
            )
            log.info(
                "Transcription complete: path=%s lang=%s dur=%.2fs text_len=%d",
                path,
                result.language,
                result.duration_seconds,
                len(result.text),
            )
            return result
        except Exception:
            log.exception("Transcription failed: path=%s model=%s", path, self._model)
            raise
