"""Streaming state primitives — IntermediateTextState, StreamState, error helpers.

Extracted from _shared_streaming.py (Issue #760).  These types represent the
mutable state of a single streaming turn; they carry no platform knowledge and
import nothing from the platform layer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from lyra.core.messaging.message import GENERIC_ERROR_REPLY

log = logging.getLogger(__name__)


# Seconds between intermediate streaming edits (debounce).
# Shared by Telegram and Discord adapters; aligned with each platform's rate limit.
STREAMING_EDIT_INTERVAL = 1.0


# Maximum accumulated intermediate text length. Segments beyond this are
# silently dropped — the display is a streaming placeholder, not a transcript.
_MAX_INTERMEDIATE_CHARS = 8_000


class IntermediateTextState:
    """Tracks accumulated intermediate text for streaming display.

    Extracted from TelegramAdapter and DiscordAdapter to eliminate identical
    intermediate-text accumulation and formatting logic. Token deltas are
    concatenated raw — Anthropic's text deltas already carry their own
    whitespace, so adjacent chunks reconstruct the message naturally. A
    single leading ⏳ marks the message as still streaming.

    Usage::

        state = IntermediateTextState()
        state.append(event.delta)          # on TextDeltaRenderEvent
        display = state.display()          # accumulated intermediate text
    """

    def __init__(self) -> None:
        self._text: str = ""

    @property
    def text(self) -> str:
        """Raw accumulated intermediate text."""
        return self._text

    def append(self, new_text: str) -> None:
        """Append a token delta, prefixing the buffer with ⏳ on first write.

        Silently drops segments once the accumulated text reaches
        ``_MAX_INTERMEDIATE_CHARS`` — the placeholder is for live feedback,
        not a full transcript.
        """
        if not new_text:
            return
        if len(self._text) >= _MAX_INTERMEDIATE_CHARS:
            return
        if self._text:
            self._text += new_text
        else:
            self._text = "⏳ " + new_text

    def display(self) -> str:
        """Return the accumulated intermediate text for the placeholder."""
        return self._text


def classify_stream_error(
    stream_error: Exception | None,
    *,
    had_tool_events: bool,
    final_text: str | None,
    msg_fn: Callable[[str, str], str],
) -> str | None:
    """Legacy module-level wrapper \u2014 delegates to OutboundErrorHandler.

    Kept for tests and any pre-#1279 consumers. The new code path goes through
    OutboundErrorHandler.classify_stream_error which carries its own get_msg.
    Removed at S7 of #1279.
    """
    from lyra.outbound.error_handler import OutboundErrorHandler

    handler = OutboundErrorHandler(get_msg=msg_fn)
    return handler.classify_stream_error(
        stream_error,
        had_tool_events=had_tool_events,
        final_text=final_text,
    )


@dataclass
class StreamState:
    """Mutable event-loop state for send_streaming().

    Extracted from Telegram and Discord adapters to eliminate the identical
    7-variable state block, final-text capture, and display-text assembly.
    Platform-specific rendering (API calls, text formatting) stays in each adapter.

    Usage::

        _st = StreamState()
        async for event in events:
            if isinstance(event, ToolCallStartRenderEvent | ToolCallEndRenderEvent):
                _st.had_tool_events = True
                # ... platform-specific tool render ...
            elif isinstance(event, TextDeltaRenderEvent):
                _st.istate.append(event.delta)
                # ... platform-specific intermediate edit ...
            elif isinstance(event, TextEndRenderEvent):
                _st.set_final_text(_st.istate.text)
        display_text = _st.build_display_text(adapter._msg)
    """

    had_tool_events: bool = False
    istate: IntermediateTextState = field(default_factory=IntermediateTextState)
    last_tool_edit: float | None = None
    last_intermediate_edit: float | None = None
    final_text: str | None = None
    is_error_turn: bool = False
    # Error flag set when the dispatch ladder sees ``RunErrorRenderEvent``.
    # Read at delivery time by ``build_display_text`` (not at ``TextEnd`` time)
    # because RunError arrives AFTER TextEnd in stream_processor's production
    # order: ResultLlmEvent → TextEnd (close open block, inside try) → finally
    # → RunErrorRenderEvent (post-finally, for soft errors). Reading at
    # delivery time means the order of TextEnd vs RunError does not matter.
    is_error_pending: bool = False
    stream_error: Exception | None = None

    def set_final_text(self, text: str, *, is_error: bool = False) -> None:
        """Capture final text and error flag for the streaming turn."""
        self.final_text = text
        self.is_error_turn = is_error

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
        # Error-turn detection: is_error_turn (legacy path set at set_final_text
        # time) OR is_error_pending (RunErrorRenderEvent observed in the
        # dispatch ladder, may have arrived BEFORE or AFTER the TextEnd that
        # captured final_text — both orderings are correct).
        is_error = self.is_error_turn or self.is_error_pending
        display = ("❌ " + self.final_text) if is_error else self.final_text
        if self.stream_error is not None:
            if display:
                display += msg_fn("stream_interrupted", " [response interrupted]")
            else:
                display = msg_fn("generic", GENERIC_ERROR_REPLY)
        return display
