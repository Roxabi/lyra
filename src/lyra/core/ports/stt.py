"""STTProtocol — Domain port for speech-to-text transcription.

Self-contained: protocol + value object + errors + helpers.
Pure: stdlib + pydantic only. No inbound lyra imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class STTProtocol(Protocol):
    async def transcribe(self, audio: bytes, mime: str) -> "TranscriptionResult": ...


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration_seconds: float


class STTUnavailableError(Exception):
    """Raised when the STT NATS adapter is unreachable (timeout or connection error)."""


class STTNoiseError(Exception):
    """Raised when the transcription result is empty, too short, or a noise token.

    The STT adapter is the owner of noise detection — middleware and agents catch
    this to dispatch the stt_noise template without re-implementing the logic.
    """


_WHISPER_NOISE_TOKENS = {"[music]", "[applause]", "[laughter]", "[silence]", "[noise]"}


def is_whisper_noise(text: str) -> bool:
    """Return True if the text is empty or a known Whisper noise token."""
    stripped = text.strip().lower()
    return not stripped or stripped in _WHISPER_NOISE_TOKENS


def mime_from_suffix(suffix: str) -> str:
    """Map a file extension (with leading dot) to its audio MIME type.

    Callers that receive audio as a file path (e.g. attachment handlers) use this
    to derive the MIME type before calling STTProtocol.transcribe(audio, mime).
    """
    return {
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
        ".opus": "audio/ogg",
    }.get(suffix.lower(), "audio/ogg")


__all__ = [
    "STTProtocol",
    "TranscriptionResult",
    "STTUnavailableError",
    "STTNoiseError",
    "is_whisper_noise",
    "mime_from_suffix",
]
