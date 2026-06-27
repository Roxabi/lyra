"""TelegramFormatter — OutboundFormatter Protocol impl for Telegram."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramAPIError

from factory.adapters.telegram.telegram_formatting import _render_buttons, _render_text
from factory.adapters.telegram.telegram_rich import (
    TelegramPlaceholder,
    build_rich_message,
    build_thinking_message,
    chunk_markdown,
    edit_text_with_fallback,
    resolve_draft_id,
    rich_messages_enabled,
    send_markdownv2_text,
    send_rich_draft,
    send_text_with_fallback,
    send_thinking_with_fallback,
    use_rich_draft,
)
from factory.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from factory.outbound._reasoning_accum import REASONING_MAX_LEN, ReasoningAccumulator
from factory.outbound.formatter import BaseFormatter

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.adapters.telegram import TelegramAdapter

log = logging.getLogger("factory.adapters.telegram")


def _message_id(ph: Any) -> int:
    if isinstance(ph, TelegramPlaceholder):
        if ph.message_id is None:
            raise ValueError("placeholder has no message_id")
        return ph.message_id
    return ph.message_id


class TelegramFormatter(BaseFormatter):
    """OutboundFormatter impl for Telegram (Rich Messages + MarkdownV2 fallback).

    Private chats may stream via sendRichMessageDraft (phase 2); groups use
    edit-in-place with rich_message. Reasoning trace uses <tg-thinking> HTML.
    """

    def __init__(  # noqa: PLR0913 — formatter wiring arity from _make_emitter
        self,
        adapter: "TelegramAdapter",
        chat_id: int,
        get_msg: "Callable[[str, str], str]",
        placeholder_text: str,
        reply_to: int | None = None,
        topic_id: int | None = None,
    ) -> None:
        self._adapter = adapter
        self._chat_id = chat_id
        self._get_msg = get_msg
        self._placeholder_text = placeholder_text
        self._reply_to = reply_to
        self._topic_id = topic_id
        self._reasoning = ReasoningAccumulator(
            max_len=None if rich_messages_enabled() else REASONING_MAX_LEN,
        )

    def placeholder_text(self) -> str:
        return self._placeholder_text

    def chunk(self, text: str) -> list[str]:
        if rich_messages_enabled():
            return chunk_markdown(text) or [text]
        return _render_text(text) or [text]

    def render_text(self, text: str) -> list[str]:
        return _render_text(text)

    def render_buttons(self, buttons: Any) -> Any:
        return _render_buttons(buttons)

    def dim_italic(self, text: str) -> str:
        return f"*{text}*"

    def get_msg(self, key: str, fallback: str) -> str:
        return self._get_msg(key, fallback)

    async def send_placeholder(self) -> tuple[Any, int | None]:
        if use_rich_draft(self._chat_id):
            draft_id = resolve_draft_id(self._reply_to, self._chat_id)
            ok = await send_rich_draft(
                self._adapter.bot,
                self._chat_id,
                draft_id,
                build_thinking_message(self._placeholder_text),
                topic_id=self._topic_id,
            )
            if not ok:
                sent = await send_text_with_fallback(
                    self._adapter.bot,
                    self._chat_id,
                    self._placeholder_text,
                    reply_to=self._reply_to,
                    topic_id=self._topic_id,
                )
                ph = TelegramPlaceholder(
                    chat_id=self._chat_id,
                    topic_id=self._topic_id,
                    message_id=sent.message_id,
                )
                return ph, sent.message_id
            ph = TelegramPlaceholder(
                chat_id=self._chat_id,
                topic_id=self._topic_id,
                draft_id=draft_id,
                use_draft=True,
            )
            return ph, None
        if rich_messages_enabled():
            sent = await send_text_with_fallback(
                self._adapter.bot,
                self._chat_id,
                self._placeholder_text,
                reply_to=self._reply_to,
                topic_id=self._topic_id,
            )
            ph = TelegramPlaceholder(
                chat_id=self._chat_id,
                topic_id=self._topic_id,
                message_id=sent.message_id,
            )
            return ph, sent.message_id
        sent = await send_markdownv2_text(
            self._adapter.bot,
            self._chat_id,
            self._placeholder_text,
            reply_to=self._reply_to,
            topic_id=self._topic_id,
        )
        return sent, sent.message_id

    async def edit_placeholder_text(
        self, ph: Any, text: str, *, finalize: bool = False
    ) -> None:
        if not text:
            return
        if (
            isinstance(ph, TelegramPlaceholder)
            and ph.use_draft
            and ph.draft_id is not None
        ):
            if finalize:
                sent = await send_text_with_fallback(
                    self._adapter.bot,
                    ph.chat_id,
                    text,
                    reply_to=self._reply_to,
                    topic_id=ph.topic_id,
                )
                ph.message_id = sent.message_id
                return
            ok = await send_rich_draft(
                self._adapter.bot,
                ph.chat_id,
                ph.draft_id,
                build_rich_message(text),
                topic_id=ph.topic_id,
            )
            if not ok and ph.message_id is not None:
                await edit_text_with_fallback(
                    self._adapter.bot,
                    ph.chat_id,
                    ph.message_id,
                    text,
                )
            return
        try:
            await edit_text_with_fallback(
                self._adapter.bot,
                self._chat_id,
                _message_id(ph),
                text,
            )
        except (TelegramAPIError, ValueError) as exc:
            log.debug("Placeholder text edit skipped: type=%s", type(exc).__name__)

    async def send_trace_placeholder(self) -> tuple[Any, int | None]:
        if rich_messages_enabled():
            sent = await send_thinking_with_fallback(
                self._adapter.bot,
                self._chat_id,
                "🔧 …",
                reply_to=self._reply_to,
                topic_id=self._topic_id,
            )
            return sent, sent.message_id
        sent = await send_markdownv2_text(
            self._adapter.bot,
            self._chat_id,
            "🔧 …",
            reply_to=self._reply_to,
            topic_id=self._topic_id,
        )
        return sent, sent.message_id

    async def send_message(self, text: str) -> int | None:
        if rich_messages_enabled():
            chunks = self.chunk(text)
        else:
            chunks = _render_text(text) or [text]
        last = None
        for chunk in chunks:
            try:
                if rich_messages_enabled():
                    last = await send_text_with_fallback(
                        self._adapter.bot,
                        self._chat_id,
                        chunk,
                        topic_id=self._topic_id,
                    )
                else:
                    last = await send_markdownv2_text(
                        self._adapter.bot,
                        self._chat_id,
                        chunk,
                        topic_id=self._topic_id,
                    )
            except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: telegram-send — terminal final-chunk; type sanitized
                log.warning(
                    "Failed to send final text chunk: type=%s", type(exc).__name__
                )
        return last.message_id if last else None

    async def send_fallback(self, text: str) -> int | None:
        payload = text or self._placeholder_text
        if rich_messages_enabled():
            chunks = self.chunk(payload)
        else:
            chunks = _render_text(payload) or [payload]
        last = None
        for chunk in chunks:
            if rich_messages_enabled():
                last = await send_text_with_fallback(
                    self._adapter.bot,
                    self._chat_id,
                    chunk,
                    topic_id=self._topic_id,
                )
            else:
                last = await send_markdownv2_text(
                    self._adapter.bot,
                    self._chat_id,
                    chunk,
                    topic_id=self._topic_id,
                )
        return last.message_id if last else None

    async def _edit_trace_with_text(self, trace_obj: Any, text: str) -> None:
        if not text:
            return
        try:
            if rich_messages_enabled():
                await self._adapter.bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=trace_obj.message_id,
                    rich_message=build_thinking_message(text),
                )
            else:
                await edit_text_with_fallback(
                    self._adapter.bot,
                    self._chat_id,
                    trace_obj.message_id,
                    text,
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
        if trace_obj is None:
            return
        text, should_edit = self._reasoning.process(event)
        if should_edit and text is not None:
            # Rich <tg-thinking> is HTML — no markdown italic wrapper (#1949).
            display = text if rich_messages_enabled() else self.dim_italic(text)
            await self._edit_trace_with_text(trace_obj, display)

    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None:
        del done
        if not lines:
            return
        await self._edit_trace_with_text(trace_obj, "\n".join(lines))
