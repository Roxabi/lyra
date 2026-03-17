"""Audio helpers extracted from _shared.py.

Handles buffering of outbound audio chunks, MIME/extension utilities,
and the partial-audio error carrier used by audio outbound renderers.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from io import BytesIO

from lyra.core.message import OutboundAudio, OutboundAudioChunk

log = logging.getLogger(__name__)

# Accepted audio MIME types for inbound attachment detection.
AUDIO_MIME_TYPES = frozenset(
    {
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/opus",
        "audio/wav",
        "audio/flac",
        "audio/aac",
    }
)

# Allowed file extensions for outbound audio filenames (whitelist).
_AUDIO_EXTS = frozenset({"ogg", "mp3", "mp4", "mpeg", "opus", "wav", "flac", "aac"})

_MAX_OUTBOUND_AUDIO_BYTES: int = int(
    os.environ.get("LYRA_MAX_AUDIO_BYTES", 5 * 1024 * 1024)
)


async def buffer_audio_chunks(
    chunks: AsyncIterator[OutboundAudioChunk],
    *,
    max_bytes: int = _MAX_OUTBOUND_AUDIO_BYTES,
) -> OutboundAudio | None:
    """Buffer streamed audio chunks into a single OutboundAudio.

    Returns None if the stream yields no data. On stream error, sends
    whatever has been buffered so far (if non-empty) and re-raises so
    the caller (e.g. circuit breaker) can record the failure.
    """
    buf = BytesIO()
    caption: str | None = None
    reply_to_id: str | None = None
    mime_type = "audio/ogg"
    stream_error: Exception | None = None

    try:
        async for chunk in chunks:
            buf.write(chunk.chunk_bytes)
            caption = chunk.caption
            reply_to_id = chunk.reply_to_id
            mime_type = chunk.mime_type
            if buf.tell() > max_bytes:
                log.warning(
                    "Audio stream exceeded %d bytes, truncating",
                    max_bytes,
                )
                break
            if chunk.is_final:
                break
    except Exception as exc:
        stream_error = exc
        log.warning("Audio stream interrupted: %s", exc)

    if buf.tell() == 0:
        if stream_error is not None:
            raise stream_error
        return None

    buf.seek(0)
    assembled = OutboundAudio(
        audio_bytes=buf.read(),
        mime_type=mime_type,
        caption=caption,
        reply_to_id=reply_to_id,
    )

    if stream_error is not None:
        # Return the partial audio, but the caller must handle the re-raise
        # after sending it. We can't send here (no adapter context).
        # So we raise with the assembled audio attached.
        raise _PartialAudioError(assembled, stream_error)

    return assembled


class _PartialAudioError(Exception):
    """Internal: carries partial audio + original error for re-raise."""

    def __init__(self, audio: OutboundAudio, cause: Exception) -> None:
        super().__init__(str(cause))
        self.audio = audio
        self.cause = cause


def mime_to_ext(
    mime_type: str,
    allowed: frozenset[str],
    fallback: str = "bin",
) -> str:
    """Derive a file extension from *mime_type*, validated against *allowed*.

    Splits on ``/`` and takes the right-hand part. Returns *fallback* if the
    mime_type contains no ``/``, or if the derived extension is not in *allowed*.

    Example::

        mime_to_ext("audio/ogg", _AUDIO_EXTS)  # -> "ogg"
        mime_to_ext("application/octet-stream", _AUDIO_EXTS)  # -> "bin"
    """
    raw_ext = mime_type.split("/")[-1] if "/" in mime_type else ""
    return raw_ext if raw_ext in allowed else fallback
