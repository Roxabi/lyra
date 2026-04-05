"""Test helper factories for InboundMessage construction.

Provides make_text_message() and make_voice_message() as single points of
churn for envelope shape evolution.  All InboundAudio constructor calls will
migrate to make_voice_message() as part of issue #534.

Note: make_voice_message() imports AudioPayload from lyra.core.message.
AudioPayload does NOT exist yet — it will be added in T5 (issue #534).
The import failing at collection time is the expected RED state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lyra.core.audio_payload import AudioPayload
from lyra.core.message import InboundMessage
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMESTAMP = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_text_message(**overrides: Any) -> InboundMessage:
    """Return a default text InboundMessage with sensible test defaults.

    Any keyword argument overrides the corresponding field.
    """
    defaults: dict[str, Any] = {
        "id": "msg-1",
        "platform": "telegram",
        "bot_id": "testbot",
        "scope_id": "scope-1",
        "user_id": "user-1",
        "user_name": "Test User",
        "is_mention": False,
        "text": "hello",
        "text_raw": "hello",
        "trust_level": TrustLevel.PUBLIC,
        "modality": "text",
        "timestamp": _DEFAULT_TIMESTAMP,
    }
    defaults.update(overrides)
    return InboundMessage(**defaults)


def make_voice_message(
    *,
    audio_bytes: bytes = b"fake-ogg-bytes",
    mime_type: str = "audio/ogg",
    duration_ms: int | None = 1500,
    file_id: str | None = "file-1",
    **overrides: Any,
) -> InboundMessage:
    """Return a default voice InboundMessage with an AudioPayload attached.

    The audio field is an AudioPayload constructed from the audio_* kwargs.
    Any remaining kwargs override InboundMessage fields.

    Note: AudioPayload import at the top of this module will fail until T5
    adds AudioPayload to lyra.core.message — this is the expected RED state.
    """
    audio = AudioPayload(
        audio_bytes=audio_bytes,
        mime_type=mime_type,
        duration_ms=duration_ms,
        file_id=file_id,
        waveform_b64=None,
    )
    defaults: dict[str, Any] = {
        "id": "msg-1",
        "platform": "telegram",
        "bot_id": "testbot",
        "scope_id": "scope-1",
        "user_id": "user-1",
        "user_name": "Test User",
        "is_mention": False,
        "text": "",
        "text_raw": "",
        "trust_level": TrustLevel.PUBLIC,
        "modality": "voice",
        "audio": audio,
        "timestamp": _DEFAULT_TIMESTAMP,
    }
    defaults.update(overrides)
    return InboundMessage(**defaults)
