"""STTProtocol — Domain port for speech-to-text transcription.

Self-contained: protocol + value object + errors.
Pure: stdlib only. No inbound lyra imports.
Adapter-adjacent helpers (noise detection, MIME mapping) live in
lyra.nats.stt.stt_helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from roxabi_contracts import BlobRef


@runtime_checkable
class STTProtocol(Protocol):
    async def transcribe(self, audio: BlobRef, mime: str) -> "TranscriptionResult": ...


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration_seconds: float
    error: str = field(default="")  # non-empty on codec decode failure


class STTUnavailableError(Exception):
    """Raised when the STT NATS adapter is unreachable (timeout or connection error)."""


class STTNoiseError(Exception):
    """Raised when the transcription result is empty, too short, or a noise token.

    The STT adapter is the owner of noise detection — middleware and agents catch
    this to dispatch the stt_noise template without re-implementing the logic.

    Any positional arg is treated as an opaque human-readable message for logs
    only; callers must not parse it or rely on a `.text` attribute.
    """


__all__ = [
    "STTProtocol",
    "TranscriptionResult",
    "STTUnavailableError",
    "STTNoiseError",
]
