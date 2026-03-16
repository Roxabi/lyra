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
from typing import TYPE_CHECKING

from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundAudio,
    OutboundAudioChunk,
    Platform,
)

if TYPE_CHECKING:
    from lyra.core.messages import MessageManager

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

# Discord API message length limit — used by discord_formatting and discord_audio.
DISCORD_MAX_LENGTH = 2000

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


def chunk_text(
    text: str,
    max_len: int,
    escape_fn: Callable[[str], str] | None = None,
) -> list[str]:
    """Split *text* into chunks of at most *max_len* characters.

    Splits at the latest natural boundary before the limit (in order of
    preference): paragraph break, newline, sentence end (`. ` / `! ` / `? `),
    word boundary.  Falls back to a hard cut only if no boundary is found.

    If *escape_fn* is provided it is applied to the entire text before
    chunking (e.g. MarkdownV2 escaping), so *max_len* applies to the
    post-escape length. Callers must account for any expansion the escape
    function introduces. Returns [] for empty text.

    Raises ValueError if *max_len* is not positive.
    """
    if max_len <= 0:
        raise ValueError(f"chunk_text: max_len must be > 0, got {max_len!r}")
    if escape_fn is not None:
        text = escape_fn(text)
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while len(text) > max_len:
        window = text[:max_len]
        # Priority order: paragraph > newline > sentence end > word boundary
        cut = -1
        for sep in ("\n\n", "\n", ". ", "! ", "? ", " "):
            idx = window.rfind(sep)
            if idx > 0:
                cut = idx + len(sep)
                break
        if cut <= 0:
            cut = max_len  # hard cut as last resort
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def resolve_msg(
    manager: MessageManager | None, key: str, *, platform: str, fallback: str
) -> str:
    """Return a localised message string, falling back when no manager."""
    return manager.get(key, platform=platform) if manager is not None else fallback


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


class TypingTaskManager:
    """Manages per-channel typing indicator background tasks.

    Extracted from TelegramAdapter and DiscordAdapter to eliminate identical
    task-management logic. Each adapter keeps its own typing coroutine factory;
    this class only manages the task dict lifecycle.
    """

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}

    def start(self, chat_id: int, coro_factory: Callable[[], Awaitable[None]]) -> None:
        """Cancel any existing task for *chat_id* and start a new one."""
        existing = self._tasks.pop(chat_id, None)
        if existing and not existing.done():
            existing.cancel()
        self._tasks[chat_id] = asyncio.create_task(
            coro_factory(), name=f"typing:{chat_id}"  # type: ignore[arg-type]
        )

    def cancel(self, chat_id: int) -> None:
        """Cancel and remove the typing task for *chat_id* (no-op if absent)."""
        task = self._tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def cancel_all(self) -> None:
        """Cancel all pending typing tasks and await their completion."""
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


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
