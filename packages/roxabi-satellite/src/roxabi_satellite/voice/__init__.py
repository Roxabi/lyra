"""Voice-domain satellite plumbing (STT/TTS ingress + error replies)."""

from roxabi_satellite.voice.replies import build_stt_error_reply, build_tts_error_reply
from roxabi_satellite.voice.validation import (
    SttValidationOutcome,
    TtsValidationOutcome,
    validate_stt_request,
    validate_tts_request,
)

__all__ = [
    "SttValidationOutcome",
    "TtsValidationOutcome",
    "build_stt_error_reply",
    "build_tts_error_reply",
    "validate_stt_request",
    "validate_tts_request",
]