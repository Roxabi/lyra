"""STT protocol and types for NATS-based voice transcription."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

log = logging.getLogger(__name__)

__all__ = [
    "STTProtocol",
    "STTUnavailableError",
    "STTNoiseError",
    "TranscriptionResult",
    "STTConfig",
    "load_stt_config",
    "is_whisper_noise",
]


@runtime_checkable
class STTProtocol(Protocol):
    async def transcribe(self, path: Path | str) -> "TranscriptionResult": ...


class STTUnavailableError(Exception):
    """Raised when the STT NATS adapter is unreachable (timeout or connection error)."""


class STTNoiseError(Exception):
    """Raised when the transcription result is empty, too short, or a noise token.

    The STT adapter is the owner of noise detection — middleware and agents catch
    this to dispatch the stt_noise template without re-implementing the logic.
    """


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
    model_size = os.environ.get("LYRA_STT_MODEL") or "large-v3-turbo"
    return STTConfig(model_size=model_size)


def is_whisper_noise(text: str) -> bool:
    """Return True if the text is empty or a known Whisper noise token."""
    stripped = text.strip().lower()
    return not stripped or stripped in WHISPER_NOISE_TOKENS
