"""Voice-domain NATS contract models.

Pure Pydantic. No NATS imports. No transport logic. Every model subclasses
ContractEnvelope, which provides (contract_version, trace_id, issued_at)
plus ConfigDict(extra="ignore") for forward-compat.

See artifacts/specs/763-port-voice-domain-spec.mdx §Known drift for the
rationale on optional-but-invariant fields on response models.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

from roxabi_contracts.envelope import ContractEnvelope


class TtsRequest(ContractEnvelope):
    """TTS synthesis request. Canonical subject: ``lyra.voice.tts.request``."""

    request_id: str
    text: Annotated[str, StringConstraints(min_length=1)]
    language: str | None = None
    voice: str | None = None
    fallback_language: str | None = None
    default_language: str | None = None
    languages: list[str] | None = None
    chunked: bool = True
    engine: str | None = None
    accent: str | None = None
    personality: str | None = None
    speed: float | None = None
    emotion: str | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    segment_gap: float | None = None
    crossfade: float | None = None
    chunk_size: int | None = None


class TtsResponse(ContractEnvelope):
    """TTS synthesis response.

    Success-path invariant (asserted in test_voice_models.py): when ok=True,
    audio_b64 AND mime_type AND duration_ms are all non-null. Error-path
    (ok=False) omits them and sets error.
    """

    ok: bool
    request_id: str
    error: str | None = None
    audio_b64: str | None = None
    mime_type: str | None = None
    duration_ms: int | None = None
    waveform_b64: str | None = None


class SttRequest(ContractEnvelope):
    """STT transcription request. Canonical subject: ``lyra.voice.stt.request``."""

    request_id: str
    audio_b64: Annotated[str, StringConstraints(min_length=1)]
    model: str
    mime_type: str | None = None
    language: str | None = None
    language_detection_threshold: float | None = None
    language_detection_segments: int | None = None
    language_fallback: str | None = None


class SttResponse(ContractEnvelope):
    """STT transcription response.

    Success-path invariant (asserted in test_voice_models.py): when ok=True,
    text AND language AND duration_seconds are all non-null.
    """

    ok: bool
    request_id: str
    error: str | None = None
    text: str | None = None
    language: str | None = None
    duration_seconds: float | None = None
