"""AudioPayload — nested voice payload for unified InboundMessage (#534)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioPayload:
    """Audio payload nested inside InboundMessage when modality == 'voice'.

    Replaces the parallel InboundAudio envelope in the unified inbound path
    (issue #534). STT pipeline stage strips this field (sets to None) after
    successful transcription to keep agent history free of raw audio bytes.
    """

    audio_bytes: bytes
    mime_type: str
    duration_ms: int | None = None
    file_id: str | None = None
    waveform_b64: str | None = None
