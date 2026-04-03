"""STT service — thin wrapper around voiceCLI (faster-whisper + personal vocab)."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger(__name__)

WHISPER_NOISE_TOKENS = {"[music]", "[applause]", "[laughter]", "[silence]", "[noise]"}


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration_seconds: float


class STTConfig(BaseModel):
    model_size: str
    language_detection_threshold: float | None = None
    language_detection_segments: int | None = None
    language_fallback: str | None = None


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
        self._detection_threshold = config.language_detection_threshold
        self._detection_segments = config.language_detection_segments
        self._detection_fallback = config.language_fallback
        self._daemon_active = False
        log.debug("STTService init: model=%s (via voiceCLI)", self._model)

    async def transcribe(self, path: Path | str) -> TranscriptionResult:
        return await asyncio.to_thread(self._transcribe_sync, str(path))

    def _transcribe_sync(self, path: str) -> TranscriptionResult:
        try:
            from voicecli.config import (
                load_vocab,
                vocab_to_prompt,
            )
            from voicecli.stt_daemon import SOCKET_PATH
            from voicecli.transcribe import (
                transcribe as _transcribe,
            )

            daemon_up = SOCKET_PATH.exists()
            if daemon_up and not self._daemon_active:
                from voicecli.transcribe import unload_model  # type: ignore[import-untyped]  # noqa: I001

                unload_model()
                self._daemon_active = True
                log.info(
                    "STT daemon detected — unloaded local model,"
                    " deferring to daemon",
                )
            elif not daemon_up and self._daemon_active:
                self._daemon_active = False
                log.warning(
                    "STT daemon socket gone — falling back to in-process model",
                )

            initial_prompt = vocab_to_prompt(load_vocab())
            kwargs: dict = dict(model=self._model, initial_prompt=initial_prompt)
            if self._detection_threshold is not None:
                kwargs["language_detection_threshold"] = self._detection_threshold
            if self._detection_segments is not None:
                kwargs["language_detection_segments"] = self._detection_segments
            if self._detection_fallback is not None:
                kwargs["language_fallback"] = self._detection_fallback
            try:
                vc_result = _transcribe(Path(path), **kwargs)
            except (ConnectionError, OSError):
                if self._daemon_active:
                    self._daemon_active = False
                    log.warning(
                        "STT daemon connection failed — retrying"
                        " with in-process model",
                    )
                    vc_result = _transcribe(Path(path), **kwargs)
                else:
                    raise

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
