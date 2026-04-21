"""Streaming state primitives — IntermediateTextState, StreamState, error helpers.

Extracted from _shared_streaming.py (Issue #760).  These types represent the
mutable state of a single streaming turn; they carry no platform knowledge and
import nothing from the platform layer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from lyra.adapters.nats.nats_stream_decoder import StreamChunkTimeout
from lyra.core.messaging.message import GENERIC_ERROR_REPLY
from lyra.core.messaging.render_events import TextRenderEvent

log = logging.getLogger(__name__)


# Seconds between intermediate streaming edits (debounce).
# Shared by Telegram and Discord adapters; aligned with each platform's rate limit.
STREAMING_EDIT_INTERVAL = 1.0


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


_ERR_TIMEOUT_FALLBACK = (
    "\u23f1\ufe0f The backend took longer than 120 s to respond. Please try again."
)
_ERR_NO_FINAL_FALLBACK = (
    "\u26a0\ufe0f Response ended without a final message"
    " (tool events only). Please try again."
)


def classify_stream_error(
    stream_error: Exception | None,
    *,
    had_tool_events: bool,
    final_text: str | None,
    msg_fn: Callable[[str, str], str],
) -> str | None:
    """Return a descriptive error string for terminal error states.

    Returns ``None`` when there is no error and a final text is present
    (caller renders ``final_text`` normally).  Returns a user-facing string
    for every error branch so callers never fall through to a bare
    GENERIC_ERROR_REPLY silently.

    Args:
        stream_error:    Exception captured by the stream loop, or ``None``.
        had_tool_events: Whether tool events were seen before the error.
        final_text:      Final text captured from the stream, or ``None``.
        msg_fn:          ``get_msg(key, fallback)`` callback for i18n.
    """
    if stream_error is not None:
        if isinstance(stream_error, StreamChunkTimeout):
            return msg_fn("error_timeout", _ERR_TIMEOUT_FALLBACK)
        return msg_fn(
            "error_stream",
            f"\u26a0\ufe0f Streaming error:"
            f" {type(stream_error).__name__}: {stream_error}. Please try again.",
        )
    if final_text is None and had_tool_events:
        return msg_fn("error_no_final", _ERR_NO_FINAL_FALLBACK)
    return None


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

        Returns ``None`` when no final text was received and no classified error
        applies — callers then fall through to ``_deliver_final``'s error branch.
        Returns a descriptive error string for timeout, stream error, or tool-only
        turns so the user always sees a meaningful message.
        """
        if self.final_text is None:
            return classify_stream_error(
                self.stream_error,
                had_tool_events=self.had_tool_events,
                final_text=None,
                msg_fn=msg_fn,
            )
        display = ("❌ " + self.final_text) if self.is_error_turn else self.final_text
        if self.stream_error is not None:
            if display:
                display += msg_fn("stream_interrupted", " [response interrupted]")
            else:
                display = msg_fn("generic", GENERIC_ERROR_REPLY)
        return display
