"""STTProtocol — Domain port for speech-to-text transcription.

Moved here from lyra.stt (V8a of hexagonal remediation, ADR-059).
lyra.stt re-exports for backward compatibility.

TranscriptionResult is kept in lyra.stt to avoid pulling non-pure types
into core/ports/; it is referenced here as a string annotation only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lyra.stt import TranscriptionResult


@runtime_checkable
class STTProtocol(Protocol):
    async def transcribe(self, audio: bytes, mime: str) -> "TranscriptionResult": ...


__all__ = ["STTProtocol"]
