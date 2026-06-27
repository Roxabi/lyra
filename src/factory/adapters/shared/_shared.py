"""Shared helpers for channel adapters.

Extracted from Telegram and Discord adapters to eliminate near-identical
circuit-open / backpressure guard logic and reply_to_id parsing.

Audio helpers live in _shared_audio; text utilities live in _shared_text;
streaming state classes live in _shared_streaming.
All are re-exported here so existing importers continue to work without
changes.

push_to_hub_guarded / PushGuardDeps have been relocated to
factory.core.messaging.push_guard (ADR-073 / #1666).
TypingTaskManager has been relocated to factory.typing.task_manager.
OutboundListener has been relocated to factory.core.ports.outbound_listener.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from factory.adapters.shared._shared_audio import (
    _AUDIO_EXTS,
    _MAX_OUTBOUND_AUDIO_BYTES,
    AUDIO_MIME_TYPES,
    _PartialAudioError,
    buffer_and_render_audio,
    buffer_audio_chunks,
    mime_to_ext,
)
from factory.adapters.shared._shared_text import (
    chunk_text,
    sanitize_filename,
    truncate_caption,
)
from factory.core.messaging.push_guard import PushGuardDeps, push_to_hub_guarded
from factory.outbound._streaming_state import IntermediateTextState, StreamState
from factory.typing.task_manager import TypingTaskManager

if TYPE_CHECKING:
    from factory.core.messaging.messages import MessageManager

__all__ = [
    "AUDIO_MIME_TYPES",
    "_AUDIO_EXTS",
    "_MAX_OUTBOUND_AUDIO_BYTES",
    "_PartialAudioError",
    "buffer_and_render_audio",
    "buffer_audio_chunks",
    "mime_to_ext",
    "ATTACHMENT_EXTS_BASE",
    "DISCORD_MAX_LENGTH",
    "push_to_hub_guarded",
    "PushGuardDeps",
    "truncate_caption",
    "sanitize_filename",
    "chunk_text",
    "resolve_msg",
    "TypingTaskManager",
    "IntermediateTextState",
    "StreamState",
    "parse_reply_to_id",
    "send_with_retry",
]

log = logging.getLogger(__name__)


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


def resolve_msg(
    manager: "MessageManager | None", key: str, *, platform: str, fallback: str
) -> str:
    """Return a localised message string, falling back when no manager."""
    return manager.get(key, platform=platform) if manager is not None else fallback


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


async def send_with_retry(
    coro_fn: Callable[[], Any],
    *,
    label: str,
    max_attempts: int = 3,
) -> None:
    """Call *coro_fn()* and retry with exponential backoff (1 s, 2 s, 4 s ...).

    Swallows the final exception and returns normally after exhaustion. Use only
    for cosmetic/intermediate operations where silent skip is acceptable (e.g.
    streaming edits, tool embeds). For operations whose return value drives
    routing (e.g. updating reply_message_id), bypass this function and use a
    bare try/except instead.
    """
    for attempt in range(max_attempts):
        try:
            await coro_fn()
            return
        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: adapter-retry — cosmetic retry; type sanitized
            if attempt == max_attempts - 1:
                log.warning(
                    "%s failed after %d attempts: type=%s",
                    label,
                    max_attempts,
                    type(exc).__name__,
                )
                return
            delay = 2**attempt  # 1 s, 2 s, 4 s ...
            log.warning(
                "%s failed (attempt %d/%d), retrying in %d s",
                label,
                attempt + 1,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)
