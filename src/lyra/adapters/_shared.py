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
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

# Re-exports from _shared_audio — importers can use either module.
from lyra.adapters._shared_audio import (
    _AUDIO_EXTS,
    _MAX_OUTBOUND_AUDIO_BYTES,
    AUDIO_MIME_TYPES,
    _PartialAudioError,
    buffer_and_render_audio,
    buffer_audio_chunks,
    mime_to_ext,
)
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    InboundAudio,
    InboundMessage,
    Platform,
)
from lyra.core.render_events import TextRenderEvent

if TYPE_CHECKING:
    from lyra.core.bus import Bus
    from lyra.core.messages import MessageManager

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
    "parse_reply_to_id",
]

log = logging.getLogger(__name__)


async def push_to_hub_guarded(  # noqa: PLR0913 — each arg is a distinct guard/callback dependency
    *,
    inbound_bus: "Bus[Any]",
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
        await inbound_bus.put(platform, msg)
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

# Seconds between intermediate streaming edits (debounce).
# Shared by Telegram and Discord adapters; aligned with each platform's rate limit.
STREAMING_EDIT_INTERVAL = 1.0


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


# Maximum accumulated intermediate text length. Segments beyond this are
# silently dropped — the display is a streaming placeholder, not a transcript.
_MAX_INTERMEDIATE_CHARS = 8_000


class IntermediateTextState:
    """Tracks accumulated intermediate text and tool recap for streaming display.

    Extracted from TelegramAdapter and DiscordAdapter to eliminate identical
    intermediate-text accumulation and formatting logic. Each new segment gets
    a ⏳ prefix and is separated by a newline so multiple thoughts stack
    vertically. The latest tool recap can optionally be combined below the text.

    Usage::

        state = IntermediateTextState()
        state.append(event.text)           # on TextRenderEvent(is_final=False)
        state.set_tool_summary(recap)      # on ToolSummaryRenderEvent
        display = state.display()          # text + recap (Telegram)
        display = state.display(combine_recap=False)  # text only (Discord)
    """

    def __init__(self) -> None:
        self._text: str = ""
        self._tool_summary: str = ""

    @property
    def has_intermediate_text(self) -> bool:
        """True once at least one intermediate segment has been appended."""
        return bool(self._text)

    @property
    def text(self) -> str:
        """Raw accumulated intermediate text (without tool summary)."""
        return self._text

    def append(self, new_text: str) -> None:
        """Append a new intermediate segment with ⏳ prefix on a new line.

        Silently drops segments once the accumulated text reaches
        ``_MAX_INTERMEDIATE_CHARS`` — the placeholder is for live feedback,
        not a full transcript.
        """
        if not new_text:
            return
        if len(self._text) >= _MAX_INTERMEDIATE_CHARS:
            return
        if self._text:
            self._text += "\n⏳ " + new_text
        else:
            self._text = "⏳ " + new_text

    def set_tool_summary(self, summary: str) -> None:
        """Update the latest tool recap text."""
        self._tool_summary = summary

    def display(self, *, combine_recap: bool = True) -> str:
        """Return the string to render in the placeholder message.

        When *combine_recap* is ``True`` (default) and both intermediate text
        and a tool recap are present, they are joined with a blank line so both
        remain visible in the same message edit.  Pass ``combine_recap=False``
        on Discord, where the recap lives in a separate embed.
        """
        if combine_recap and self._text and self._tool_summary:
            return self._text + "\n\n" + self._tool_summary
        return self._text or self._tool_summary


@dataclass
class StreamState:
    """Mutable event-loop state for send_streaming().

    Extracted from Telegram and Discord adapters to eliminate the identical
    7-variable state block, final-text capture, and display-text assembly.
    Platform-specific rendering (API calls, text formatting) stays in each adapter.

    Usage::

        _st = StreamState()
        async for event in events:
            if isinstance(event, ToolSummaryRenderEvent):
                _st.had_tool_events = True
                # ... platform-specific tool render ...
            else:
                if event.is_final:
                    _st.on_final_text(event)
                else:
                    _st.istate.append(event.text)
                    # ... platform-specific intermediate edit ...
        display_text = _st.build_display_text(adapter._msg)
    """

    had_tool_events: bool = False
    istate: IntermediateTextState = field(default_factory=IntermediateTextState)
    last_tool_edit: float | None = None
    last_intermediate_edit: float | None = None
    final_text: str | None = None
    is_error_turn: bool = False
    stream_error: Exception | None = None

    def on_final_text(self, event: TextRenderEvent) -> None:
        """Capture final text and error flag from a terminal TextRenderEvent."""
        self.final_text = event.text
        self.is_error_turn = event.is_error

    def build_display_text(self, msg_fn: Callable[[str, str], str]) -> str | None:
        """Assemble display text with error prefix and interrupt notice.

        Returns ``None`` when no final text was received — callers handle the
        error-only case (``elif stream_error``) separately.
        """
        if self.final_text is None:
            return None
        display = ("❌ " + self.final_text) if self.is_error_turn else self.final_text
        if self.stream_error is not None:
            if display:
                display += msg_fn("stream_interrupted", " [response interrupted]")
            else:
                display = msg_fn("generic", GENERIC_ERROR_REPLY)
        return display


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
