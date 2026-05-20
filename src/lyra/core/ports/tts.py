"""TtsProtocol — Domain port for text-to-speech synthesis.

Self-contained: protocol + value object + error.
Pure: stdlib + pydantic + AgentTTSConfig (TYPE_CHECKING only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import AgentTTSConfig


@runtime_checkable
class TtsProtocol(Protocol):
    async def synthesize(
        self,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> "SynthesisResult": ...


@dataclass
class SynthesisResult:
    audio_bytes: bytes
    mime_type: str
    duration_ms: int | None  # None if WAV header unreadable
    waveform_b64: str | None = field(default=None)  # 256-byte amplitude array, base64
    error: str = field(default="")  # non-empty on codec decode failure


class TtsUnavailableError(Exception):
    """Raised when the TTS NATS adapter is unreachable (timeout or connection error)."""


__all__ = [
    "TtsProtocol",
    "SynthesisResult",
    "TtsUnavailableError",
]
