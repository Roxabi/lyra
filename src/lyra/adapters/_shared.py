"""Shared helpers for channel adapters.

Extracted from Telegram and Discord adapters to eliminate near-identical
circuit-open / backpressure guard logic and reply_to_id parsing.

Audio helpers live in _shared_audio; they are re-exported here so existing
importers continue to work without changes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

# Re-exports from _shared_audio — importers can use either module.
from lyra.adapters._shared_audio import (
    _AUDIO_EXTS,
    _MAX_OUTBOUND_AUDIO_BYTES,
    AUDIO_MIME_TYPES,
    _PartialAudioError,
    buffer_audio_chunks,
    mime_to_ext,
)
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    Platform,
)

if TYPE_CHECKING:
    from lyra.core.messages import MessageManager

__all__ = [
    "AUDIO_MIME_TYPES",
    "_AUDIO_EXTS",
    "_MAX_OUTBOUND_AUDIO_BYTES",
    "_PartialAudioError",
    "buffer_audio_chunks",
    "mime_to_ext",
    "ATTACHMENT_EXTS_BASE",
    "DISCORD_MAX_LENGTH",
    "push_to_hub_guarded",
    "truncate_caption",
    "sanitize_filename",
    "chunk_text",
    "resolve_msg",
    "TypingTaskManager",
    "parse_reply_to_id",
]

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
            text = get_msg(
                "circuit_open_ack",
                "I'm temporarily overloaded, please try again in a moment.",
            )
            await send_backpressure(text)
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
    manager: "MessageManager | None", key: str, *, platform: str, fallback: str
) -> str:
    """Return a localised message string, falling back when no manager."""
    return manager.get(key, platform=platform) if manager is not None else fallback


class TypingTaskManager:
    """Manages per-channel typing indicator background tasks.

    Extracted from TelegramAdapter and DiscordAdapter to eliminate identical
    task-management logic. Each adapter keeps its own typing coroutine factory;
    this class only manages the task dict lifecycle.
    """

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def start(self, chat_id: int, coro_factory: Callable[[], Awaitable[None]]) -> None:
        """Cancel any existing task for *chat_id* and start a new one."""
        existing = self._tasks.pop(chat_id, None)
        if existing and not existing.done():
            existing.cancel()
        self._tasks[chat_id] = asyncio.create_task(
            coro_factory(),  # type: ignore[arg-type]
            name=f"typing:{chat_id}",
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
