"""Whisper-specific helpers for STT noise detection + MIME mapping.

Adapter layer (lyra.nats), not port layer.
"""

WHISPER_NOISE_TOKENS = {"[music]", "[applause]", "[laughter]", "[silence]", "[noise]"}


def is_whisper_noise(text: str) -> bool:
    """Return True if the text is empty or a known Whisper noise token."""
    stripped = text.strip().lower()
    return not stripped or stripped in WHISPER_NOISE_TOKENS


def mime_from_suffix(suffix: str) -> str:
    """Map a file extension (with leading dot) to its audio MIME type.

    Callers that receive audio as a file path (e.g. attachment handlers) use this
    to derive the MIME type before calling STTProtocol.transcribe(blob_ref, mime).
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
