"""TelegramFormatter — OutboundFormatter Protocol impl for Telegram."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramAPIError

from factory.adapters.telegram.telegram_formatting import _render_buttons, _render_text
from factory.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from factory.outbound._reasoning_accum import ReasoningAccumulator

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.adapters.telegram import TelegramAdapter

log = logging.getLogger("factory.adapters.telegram")


class TelegramFormatter:
    """OutboundFormatter impl for Telegram (MarkdownV2 escape, 4096 chunk).

    Encapsulates chunking, escaping, button rendering, trace rendering
    (reasoning + tool recap), and send-mechanics for the Telegram platform.

    Per-formatter (per-turn) state: reasoning accumulator + last-edit timestamp.
    Both reset on ReasoningStartRenderEvent so each turn starts clean.
    """

    def __init__(
        self,
        adapter: "TelegramAdapter",
        chat_id: int,
        get_msg: "Callable[[str, str], str]",
        placeholder_text: str,
        reply_to: int | None = None,
    ) -> None:
        self._adapter = adapter
        self._chat_id = chat_id
        self._get_msg = get_msg
        self._placeholder_text = placeholder_text
        self._reply_to = reply_to
        self._reasoning = ReasoningAccumulator()

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

    def get_msg(self, key: str, fallback: str) -> str:
        return self._get_msg(key, fallback)

    async def send_placeholder(self) -> tuple[Any, int]:
        kw: dict = {}
        if self._reply_to is not None:
            kw["reply_to_message_id"] = self._reply_to
        msg = await self._adapter.bot.send_message(
            chat_id=self._chat_id,
            text=self._placeholder_text,
            **kw,
        )
        return msg, msg.message_id

    async def edit_placeholder_text(self, ph: Any, text: str) -> None:
        rendered = _render_text(text)
        if rendered:
            try:
                await self._adapter.bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=ph.message_id,
                    text=rendered[0],
                    parse_mode="MarkdownV2",
                )
            except TelegramAPIError as exc:
                log.debug("Placeholder text edit skipped: type=%s", type(exc).__name__)

    async def send_trace_placeholder(self) -> tuple[Any, int | None]:
        kw: dict = {}
        if self._reply_to is not None:
            kw["reply_to_message_id"] = self._reply_to
        msg = await self._adapter.bot.send_message(
            chat_id=self._chat_id,
            text="🔧 …",
            **kw,
        )
        return msg, msg.message_id

    async def send_message(self, text: str) -> int | None:
        rendered = _render_text(text)
        last = None
        for chunk in rendered:
            try:
                last = await self._adapter.bot.send_message(
                    chat_id=self._chat_id, text=chunk, parse_mode="MarkdownV2"
                )
            except Exception as exc:  # noqa: BLE001 — terminal final-chunk send; type sanitized
                log.warning(
                    "Failed to send final text chunk: type=%s", type(exc).__name__
                )
        return last.message_id if last else None

    async def send_fallback(self, text: str) -> int | None:
        rendered = _render_text(text) if text else []
        if not rendered:
            rendered = [text or self._placeholder_text]
        last = None
        for chunk in rendered:
            last = await self._adapter.bot.send_message(
                chat_id=self._chat_id, text=chunk, parse_mode="MarkdownV2"
            )
        return last.message_id if last else None

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
        if trace_obj is None:
            return
        text, should_edit = self._reasoning.process(event)
        if should_edit and text is not None:
            await self._edit_trace_with_text(trace_obj, self.dim_italic(text))

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
