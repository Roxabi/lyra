"""TelegramFormatter — OutboundFormatter Protocol impl for Telegram."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramAPIError

from lyra.adapters.telegram.telegram_formatting import _render_buttons, _render_text
from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from lyra.outbound.throttle import STREAMING_EDIT_INTERVAL

if TYPE_CHECKING:
    from collections.abc import Callable

    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger("lyra.adapters.telegram")


class TelegramFormatter:
    """OutboundFormatter impl for Telegram (MarkdownV2 escape, 4096 chunk).

    Encapsulates chunking, escaping, button rendering, and trace rendering
    (reasoning + tool recap) for the Telegram platform. Send-mechanics
    callbacks (send_placeholder, edit_placeholder_text, …) stay in
    build_streaming_callbacks for the transition period (S4→S6).

    Per-formatter (per-turn) state: reasoning accumulator + last-edit timestamp.
    Both reset on ReasoningStartRenderEvent so each turn starts clean.
    """

    def __init__(
        self,
        adapter: "TelegramAdapter",
        chat_id: int,
        get_msg: "Callable[[str, str], str]",
        placeholder_text: str,
    ) -> None:
        self._adapter = adapter
        self._chat_id = chat_id
        self._get_msg = get_msg
        self._placeholder_text = placeholder_text
        self._reasoning_accum: str = ""
        self._last_reasoning_edit: float | None = None

    def placeholder_text(self) -> str:
        return self._placeholder_text

    def chunk(self, text: str) -> list[str]:
        return _render_text(text) or [text]

    def render_text(self, text: str) -> list[str]:
        return _render_text(text)

    def render_buttons(self, buttons: Any) -> Any:
        return _render_buttons(buttons)

    def dim_italic(self, text: str) -> str:
        return f"*{text}*"

    async def _edit_trace_with_text(self, trace_obj: Any, text: str) -> None:
        """Edit the trace placeholder with formatted text."""
        rendered = _render_text(text)
        if rendered:
            try:
                await self._adapter.bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=trace_obj.message_id,
                    text=rendered[0],
                    parse_mode="MarkdownV2",
                )
            except TelegramAPIError as exc:
                log.debug("Reasoning trace edit skipped: type=%s", type(exc).__name__)

    async def edit_reasoning(
        self,
        trace_obj: Any,
        event: ReasoningStartRenderEvent
        | ReasoningDeltaRenderEvent
        | ReasoningEndRenderEvent,
    ) -> None:
        """Render reasoning events as dim italic text in the trace placeholder.

        Slice 4 (#1101): the show_intermediate=False gate lives upstream on
        StreamProcessor — no Reasoning* events reach here when disabled.
        Delta edits are throttled by STREAMING_EDIT_INTERVAL. Accumulated
        text is truncated to 120 chars with '…' suffix.

        trace_obj=None means placeholder send failed — bail silently.
        """
        if isinstance(event, ReasoningStartRenderEvent):
            if trace_obj is None:
                return
            self._reasoning_accum = ""
            self._last_reasoning_edit = None

        elif isinstance(event, ReasoningDeltaRenderEvent):
            if trace_obj is None:
                return
            self._reasoning_accum += event.delta
            truncated = self._reasoning_accum
            if len(truncated) > 120:
                truncated = truncated[:117] + "…"
            now = time.monotonic()
            if (
                self._last_reasoning_edit is None
                or (now - self._last_reasoning_edit) >= STREAMING_EDIT_INTERVAL
            ):
                await self._edit_trace_with_text(trace_obj, self.dim_italic(truncated))
                self._last_reasoning_edit = now

        else:  # ReasoningEndRenderEvent
            if trace_obj is None:
                return
            if self._reasoning_accum:
                truncated = self._reasoning_accum
                if len(truncated) > 120:
                    truncated = truncated[:117] + "…"
                await self._edit_trace_with_text(trace_obj, self.dim_italic(truncated))

    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None:
        """Render tool recap card lines into the trace placeholder."""
        del done  # header is already part of lines (per format_recap_lines)
        if not lines:
            return
        text = "\n".join(lines)
        rendered = _render_text(text)
        if not rendered:
            return
        try:
            await self._adapter.bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=trace_obj.message_id,
                text=rendered[0],
                parse_mode="MarkdownV2",
            )
        except TelegramAPIError as exc:
            log.debug("Tool recap edit skipped: type=%s", type(exc).__name__)
