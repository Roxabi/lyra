"""Shared streaming algorithm for outbound adapters.

Extracted from TelegramAdapter and DiscordAdapter to eliminate near-identical
send_streaming() implementations. Platform-specific behaviour is injected via
PlatformCallbacks; the algorithm is the same for all platforms.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lyra.adapters._shared import (
    STREAMING_EDIT_INTERVAL,
    StreamState,
    format_tool_summary_header,
)
from lyra.core.render_events import TextRenderEvent, ToolSummaryRenderEvent

if TYPE_CHECKING:
    from lyra.core.message import OutboundMessage
    from lyra.core.render_events import RenderEvent

log = logging.getLogger(__name__)

__all__ = ["PlatformCallbacks", "StreamingSession"]


@dataclass
class PlatformCallbacks:
    """Platform-specific I/O callbacks injected into StreamingSession.

    All coroutine fields must be awaitable. Sync fields (chunk_text,
    start_typing, cancel_typing) are called directly.
    """

    # Send the initial placeholder message.
    # Returns (placeholder_obj, reply_message_id | None).
    send_placeholder: Callable[[], Awaitable[tuple[Any, int | None]]]

    # Edit the placeholder with accumulated intermediate text.
    # Args: (placeholder_obj, display_text)
    edit_placeholder_text: Callable[[Any, str], Awaitable[None]]

    # Edit the placeholder with a tool summary embed/text.
    # Args: (placeholder_obj, event, header_text)
    edit_placeholder_tool: Callable[[Any, ToolSummaryRenderEvent, str], Awaitable[None]]

    # Send a new message (used for final text in tool-using turns).
    # Returns the sent message id (or None).
    send_message: Callable[[str], Awaitable[int | None]]

    # Fallback: send final text without streaming (placeholder failed).
    # Returns the sent message id (or None).
    send_fallback: Callable[[str], Awaitable[int | None]]

    # Split text into chunks that fit platform message limits.
    chunk_text: Callable[[str], list[str]]

    # Start (or restart) the typing indicator. Sync.
    start_typing: Callable[[], None]

    # Cancel the typing indicator. Sync.
    cancel_typing: Callable[[], None]


class StreamingSession:
    """Executes the shared streaming algorithm using platform-injected callbacks.

    Usage::

        session = StreamingSession(callbacks, outbound)
        await session.run(events)
    """

    def __init__(
        self,
        callbacks: PlatformCallbacks,
        outbound: "OutboundMessage | None",
    ) -> None:
        self._cb = callbacks
        self._outbound = outbound
        self._st: StreamState | None = None

    async def run(self, events: "AsyncIterator[RenderEvent]") -> None:
        """Execute the full streaming protocol."""
        placeholder_obj, reply_id = await self._send_placeholder(events)
        if placeholder_obj is None:
            # Fallback path already completed inside _send_placeholder
            return
        if reply_id is not None and self._outbound is not None:
            self._outbound.metadata["reply_message_id"] = reply_id
        await self._run_event_loop(events, placeholder_obj)
        await self._deliver_final(placeholder_obj)
        self._handle_typing_tail()

    async def _send_placeholder(
        self,
        events: "AsyncIterator[RenderEvent]",
    ) -> tuple[Any, int | None]:
        """Send placeholder. On failure, drain events and call send_fallback.

        Returns (placeholder_obj, reply_id) on success.
        Returns (None, None) on failure (fallback path taken).
        """
        try:
            placeholder_obj, reply_id = await self._cb.send_placeholder()
            return placeholder_obj, reply_id
        except Exception:
            log.exception("Failed to send placeholder — falling back to non-streaming")
            parts: list[str] = []
            async for event in events:
                if isinstance(event, TextRenderEvent):
                    parts.append(event.text)
            fallback_text = "".join(parts)
            fallback_id = await self._cb.send_fallback(fallback_text)
            if self._outbound is not None and fallback_id is not None:
                self._outbound.metadata["reply_message_id"] = fallback_id
            return None, None

    async def _run_event_loop(
        self,
        events: "AsyncIterator[RenderEvent]",
        placeholder_obj: Any,
    ) -> None:
        """Process events, updating placeholder and accumulating state."""
        _st = StreamState()
        self._st = _st

        try:
            async for event in events:
                if isinstance(event, ToolSummaryRenderEvent):
                    _st.had_tool_events = True
                    now = time.monotonic()
                    if (
                        event.is_complete
                        or _st.last_tool_edit is None
                        or (now - _st.last_tool_edit) >= STREAMING_EDIT_INTERVAL
                    ):
                        header = format_tool_summary_header(event)
                        try:
                            await self._cb.edit_placeholder_tool(
                                placeholder_obj, event, header
                            )
                            _st.last_tool_edit = now
                        except Exception as exc:
                            log.debug("Tool edit skipped: %s", exc)
                else:  # TextRenderEvent
                    if event.is_final:
                        _st.on_final_text(event)
                    else:
                        _st.istate.append(event.text)
                        now = time.monotonic()
                        if (
                            _st.last_intermediate_edit is None
                            or (now - _st.last_intermediate_edit)
                            >= STREAMING_EDIT_INTERVAL
                        ):
                            display = _st.istate.display()
                            try:
                                await self._cb.edit_placeholder_text(
                                    placeholder_obj, display
                                )
                                _st.last_intermediate_edit = now
                            except Exception as exc:
                                log.debug("Intermediate edit skipped: %s", exc)
        except Exception as exc:
            _st.stream_error = exc
            log.exception("Stream interrupted")

    async def _deliver_final(self, placeholder_obj: Any) -> None:  # noqa: C901 — delivery: tool/text/error × chunk/overflow branches
        """Send or edit the final text after the event loop completes."""
        _st = self._st
        if _st is None:
            return

        from lyra.core.message import GENERIC_ERROR_REPLY

        def _msg(_key: str, fallback: str) -> str:
            return fallback

        display_text = _st.build_display_text(_msg)

        if display_text is not None:
            final_chunks = self._cb.chunk_text(display_text) if display_text else []
            if _st.had_tool_events:
                # Tool summary in placeholder; send new message for text
                for chunk in final_chunks:
                    try:
                        msg_id = await self._cb.send_message(chunk)
                        if self._outbound is not None and msg_id is not None:
                            self._outbound.metadata["reply_message_id"] = msg_id
                    except Exception:
                        log.exception("Failed to send final text chunk")
            elif final_chunks:
                # Text-only turn: edit placeholder with first chunk
                try:
                    await self._cb.edit_placeholder_text(
                        placeholder_obj, final_chunks[0]
                    )
                except Exception:
                    log.exception("Final edit failed")
                # Send overflow chunks as new messages
                for extra_chunk in final_chunks[1:]:
                    try:
                        await self._cb.send_message(extra_chunk)
                    except Exception:
                        log.exception("Failed to send overflow chunk")
        elif _st.stream_error is not None:
            try:
                await self._cb.edit_placeholder_text(
                    placeholder_obj, GENERIC_ERROR_REPLY
                )
            except Exception:
                log.exception("Error edit failed")

        # Re-raise stream error so OutboundDispatcher can record CB failure
        if _st.stream_error is not None:
            raise _st.stream_error

    def _handle_typing_tail(self) -> None:
        """Start or cancel typing indicator based on outbound.intermediate."""
        if self._outbound is not None and self._outbound.intermediate:
            self._cb.start_typing()
        else:
            self._cb.cancel_typing()
