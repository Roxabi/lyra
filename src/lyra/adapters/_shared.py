"""Shared helpers for channel adapters.

Extracted from Telegram and Discord adapters to eliminate near-identical
circuit-open / backpressure guard logic and reply_to_id parsing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from io import BytesIO

from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundAudio,
    OutboundAudioChunk,
    Platform,
)

log = logging.getLogger(__name__)


async def push_to_hub_guarded(  # noqa: PLR0913 — each arg is a distinct guard/callback dependency
    *,
    inbound_bus: object,
    platform: Platform,
    msg: InboundMessage | InboundAudio,
    circuit_registry: CircuitRegistry | None,
    on_drop: Callable[[], None] | None,
    send_backpressure: Callable[[str], Awaitable[None]],
    get_msg: Callable[[str, str], str],
) -> None:
    """Put *msg* on the inbound bus with circuit-open and backpressure guards.

    *on_drop* is called before early return in both circuit-open and QueueFull
    cases. *send_backpressure* sends the backpressure ack to the user.
    Always returns normally.
    """
    if circuit_registry is not None:
        cb = circuit_registry.get("hub")
        if cb is not None and cb.is_open():
            log.warning(
                "hub_circuit_open",
                extra={
                    "platform": platform.value,
                    "user_id": msg.user_id,
                    "dropped": True,
                },
            )
            if on_drop is not None:
                on_drop()
            return

    try:
        inbound_bus.put(platform, msg)  # type: ignore[attr-defined]
    except asyncio.QueueFull:
        if on_drop is not None:
            on_drop()
        text = get_msg("backpressure_ack", "Processing your request\u2026")
        await send_backpressure(text)


def truncate_caption(caption: str | None, limit: int) -> str | None:
    """Truncate caption to *limit* characters, returning None if empty."""
    if not caption:
        return None
    return caption[:limit]


def sanitize_filename(
    filename: str,
    allowed_exts: frozenset[str],
    fallback: str = "attachment.bin",
) -> str:
    """Sanitize a caller-supplied filename for outbound attachments.

    Strips path components, control characters, and validates the
    extension against *allowed_exts*. Returns *fallback* if the
    result is empty or the extension is not whitelisted.
    """
    # Strip path components (defense against ../../ traversal)
    name = os.path.basename(filename)
    # Strip control characters and null bytes
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    # Enforce length cap
    name = name[:255]

    if not name:
        return fallback

    # Validate extension against whitelist
    _, ext = os.path.splitext(name)
    ext_clean = ext.lstrip(".").lower()
    if ext_clean not in allowed_exts:
        return fallback

    return name


# Shared base set of allowed file extensions for outbound attachment filenames.
# Adapters may extend this with platform-specific extensions.
ATTACHMENT_EXTS_BASE = frozenset(
    {
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "bmp",  # image
        "mp4",
        "webm",
        "mov",
        "avi",  # video
        "pdf",
        "txt",
        "csv",
        "json",
        "xml",
        "zip",
        "tar",
        "gz",  # document/file
    }
)


# Allowed file extensions for outbound audio filenames (whitelist).
AUDIO_EXTS = frozenset({"ogg", "mp3", "mp4", "mpeg", "opus", "wav", "flac", "aac"})

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



def parse_reply_to_id(reply_to_id: str | None) -> int | None:
    """Parse a string reply_to_id into an int, returning None on bad input."""
    if reply_to_id is None:
        return None
    try:
        return int(reply_to_id)
    except ValueError:
        log.warning(
            "parse_reply_to_id: invalid reply_to_id=%r, ignoring",
            reply_to_id,
        )
        return None
