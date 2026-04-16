"""Shared helpers for channel adapters.

Extracted from Telegram and Discord adapters to eliminate near-identical
circuit-open / backpressure guard logic and reply_to_id parsing.

Audio helpers live in _shared_audio; text utilities live in _shared_text;
streaming state classes live in _shared_streaming.
All are re-exported here so existing importers continue to work without
changes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any

from lyra.adapters._shared_audio import (
    _AUDIO_EXTS,
    _MAX_OUTBOUND_AUDIO_BYTES,
    AUDIO_MIME_TYPES,
    _PartialAudioError,
    buffer_and_render_audio,
    buffer_audio_chunks,
    mime_to_ext,
)

# Re-exports from _shared_streaming — importers can use either module.
from lyra.adapters._shared_streaming import (
    STREAMING_EDIT_INTERVAL,
    IntermediateTextState,
    StreamState,
)

# Re-exports from _shared_audio — importers can use either module.
from lyra.adapters._shared_text import chunk_text, sanitize_filename, truncate_caption
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import InboundMessage, Platform

if TYPE_CHECKING:
    from lyra.adapters.outbound_listener import OutboundListener
    from lyra.core.bus import Bus
    from lyra.core.messages import MessageManager
    from lyra.core.render_events import ToolSummaryRenderEvent

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
    "STREAMING_EDIT_INTERVAL",
    "push_to_hub_guarded",
    "truncate_caption",
    "sanitize_filename",
    "chunk_text",
    "resolve_msg",
    "TypingTaskManager",
    "IntermediateTextState",
    "StreamState",
    "format_tool_summary_header",
    "parse_reply_to_id",
    "send_with_retry",
]

log = logging.getLogger(__name__)


async def push_to_hub_guarded(  # noqa: PLR0913 — each arg is a distinct guard/callback dependency
    *,
    inbound_bus: "Bus[Any]",
    platform: Platform,
    msg: InboundMessage,
    circuit_registry: CircuitRegistry | None,
    on_drop: Callable[[], None] | None,
    send_backpressure: Callable[[str], Awaitable[None]],
    get_msg: Callable[[str, str], str],
    outbound_listener: "OutboundListener | None" = None,
) -> None:
    """Put *msg* on the inbound bus with circuit-open and backpressure guards.

    *on_drop* is called before early return in both circuit-open and QueueFull
    cases. *send_backpressure* sends the backpressure ack to the user.
    Always returns normally.

    *outbound_listener* — when provided, ``cache_inbound(msg)`` is called
    before enqueuing so that outbound NATS correlation can resolve the original
    message by stream_id.  Must be called here (not by the caller) to guarantee
    the cache is populated before the hub can dispatch a response.
    """
    if outbound_listener is not None:
        outbound_listener.cache_inbound(msg)

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
        await inbound_bus.put(platform, msg)
    except asyncio.QueueFull:
        if on_drop is not None:
            on_drop()
        text = get_msg("backpressure_ack", "Processing your request\u2026")
        await send_backpressure(text)


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


class TypingTaskManager:
    """Manages per-channel typing indicator background tasks.

    Extracted from TelegramAdapter and DiscordAdapter to eliminate identical
    task-management logic. Each adapter keeps its own typing coroutine factory;
    this class only manages the task dict lifecycle.
    """

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def start(
        self,
        chat_id: int,
        coro_factory: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Cancel any existing task for *chat_id* and start a new one."""
        existing = self._tasks.pop(chat_id, None)
        if existing and not existing.done():
            existing.cancel()
        self._tasks[chat_id] = asyncio.create_task(
            coro_factory(),
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
        except Exception:
            if attempt == max_attempts - 1:
                log.exception("%s failed after %d attempts", label, max_attempts)
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


def format_tool_summary_header(event: ToolSummaryRenderEvent) -> str:
    """Return the tool summary header string for a ToolSummaryRenderEvent.

    Header only — does NOT include tool body lines.
    """
    return "🔧 Done ✅" if event.is_complete else "🔧 Working…"
