"""TtsProtocol — Domain port for text-to-speech synthesis.

Self-contained: protocol + value object + errors.
Pure: stdlib + pydantic + AgentTTSConfig (TYPE_CHECKING only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from roxabi_contracts import BlobRef

if TYPE_CHECKING:
    from factory.core.agent.agent_config import AgentTTSConfig


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
    blob_ref: BlobRef | None
    mime_type: str
    duration_ms: int | None  # None if WAV header unreadable
    waveform_b64: str | None = field(default=None)  # 256-byte amplitude array, base64
    # Error fields — non-empty on failure; empty string means success.
    error: str = field(default="")  # error code (e.g. "pool.no_live_workers")
    error_message: str = field(default="")  # human-readable, safe to surface
    error_detail: str | None = field(default=None)  # optional structured detail
    retryable: bool = field(default=True)
    # True  → transport/worker-unavailable (TtsUnavailableError)
    # False → worker domain error with non-empty error (TtsSynthesisError)
    unavailable: bool = field(default=False)


class TtsUnavailableError(Exception):
    """Raised when the TTS NATS adapter is unreachable (timeout or connection error)."""


class TtsSynthesisError(Exception):
    """Raised when the TTS worker responded but synthesis failed (domain error).

    Distinct from TtsUnavailableError: the worker was reachable but rejected
    the request (e.g. invalid voice, unsupported language, content policy).
    The ``message`` field is already credential-scrubbed and length-truncated
    by the contract validator — safe to surface to users.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        detail: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail
        self.retryable = retryable

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


__all__ = [
    "TtsProtocol",
    "SynthesisResult",
    "TtsUnavailableError",
    "TtsSynthesisError",
]
