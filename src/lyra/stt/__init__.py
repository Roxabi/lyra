"""STT service — faster-whisper transcription with GPU/CPU auto-detection."""

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
    model_size: str = "small"
    device: str = "auto"  # "auto" | "cuda" | "cpu"
    compute_type: str = "auto"  # "auto" | "float16" | "int8"

    def validate(self) -> None:
        if self.device == "cpu" and self.compute_type == "float16":
            raise ValueError(
                "compute_type='float16' requires CUDA — use 'int8' or 'auto' for CPU"
            )
        if self.device == "cuda" and self.compute_type == "int8":
            raise ValueError(
                "compute_type='int8' is CPU-only — use 'float16' or 'auto' for CUDA"
            )


def load_stt_config() -> STTConfig:
    return STTConfig(
        model_size=os.environ.get("STT_MODEL_SIZE", "small"),
        device=os.environ.get("STT_DEVICE", "auto"),
        compute_type=os.environ.get("STT_COMPUTE_TYPE", "auto"),
    )


def is_whisper_noise(text: str) -> bool:
    """Return True if the text is empty or a known Whisper noise token."""
    stripped = text.strip().lower()
    return not stripped or stripped in WHISPER_NOISE_TOKENS


class STTService:
    """Async STT service wrapping faster-whisper with lazy model loading."""

    def __init__(self, config: STTConfig) -> None:
        config.validate()
        self._config = config
        self._model = None

        # Resolve "auto" device
        if config.device == "auto":
            try:
                import torch  # type: ignore[import-untyped]

                self._device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                self._device = "cpu"
        else:
            self._device = config.device

        # Resolve "auto" compute_type
        if config.compute_type == "auto":
            self._compute_type = "float16" if self._device == "cuda" else "int8"
        else:
            self._compute_type = config.compute_type

        log.debug(
            "STTService init: model=%s device=%s compute=%s",
            config.model_size,
            self._device,
            self._compute_type,
        )

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info(
                "Loading WhisperModel %s on %s/%s",
                self._config.model_size,
                self._device,
                self._compute_type,
            )
            self._model = WhisperModel(
                self._config.model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
        return self._model

    async def transcribe(self, path: Path | str) -> TranscriptionResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, str(path))

    def _transcribe_sync(self, path: str) -> TranscriptionResult:
        model = self._load_model()
        segments, info = model.transcribe(path, beam_size=5)
        full_text = "".join(seg.text for seg in segments).strip()
        return TranscriptionResult(
            text=full_text,
            language=info.language,
            duration_seconds=info.duration,
        )
