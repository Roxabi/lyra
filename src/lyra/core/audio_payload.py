"""AudioPayload — nested voice payload for unified InboundMessage (#534)."""

from __future__ import annotations

from dataclasses import dataclass

from roxabi_contracts import BlobRef


@dataclass(frozen=True)
class AudioPayload:
    """Audio payload nested inside InboundMessage when modality == 'voice'.

    Replaces the parallel InboundAudio envelope in the unified inbound path
    (issue #534). STT pipeline stage strips this field (sets to None) after
    successful transcription to keep agent history free of the BlobRef pointer
    (semantics preserved — it just clears the pointer instead of the bytes).

    The blob_ref field holds a BlobRef pointer (content-addressed via BlobStore;
    or the transitional `PENDING_STORE_KEY` sentinel for legacy adapters during
    epic #1061 slices V3/V4).
    """

    blob_ref: BlobRef
    mime_type: str
    duration_ms: int | None = None
    file_id: str | None = None
    waveform_b64: str | None = None
