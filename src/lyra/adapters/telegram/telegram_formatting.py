"""Text formatting and constants for the Telegram adapter."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Callable, cast

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from lyra.adapters.shared._shared import ATTACHMENT_EXTS_BASE, chunk_text
from lyra.core.messaging.message import InboundMessage, Platform, TelegramMeta

log = logging.getLogger("lyra.adapters.telegram")

# Telegram: base extensions + audio (Telegram supports audio via send_document).
_ATTACHMENT_EXTS = ATTACHMENT_EXTS_BASE | frozenset(
    {"ogg", "mp3", "opus", "wav", "flac", "aac"}
)

TELEGRAM_MAX_LENGTH = 4096  # Telegram Bot API text message limit
TELEGRAM_CAPTION_MAX = 1024  # Telegram Bot API caption limit

_MARKDOWNV2_SPECIAL = re.compile(r"([_*\[\]()~`>#\+\-=|{}.!\\])")

# Markdown → MarkdownV2 converter (preserves bold, italic, code, etc.)
_ConvertFn = Callable[[str], str]

if TYPE_CHECKING:
    _convert_markdown: _ConvertFn

try:
    from telegramify_markdown import (  # type: ignore[import-untyped]
        markdownify as _md,
    )

    _convert_markdown = cast(_ConvertFn, _md)
except ImportError:  # pragma: no cover — fallback if dependency missing
    log.warning("telegramify-markdown not installed; Telegram formatting disabled")

    def _fallback(text: str) -> str:
        return _MARKDOWNV2_SPECIAL.sub(r"\\\1", text)

    _convert_markdown = _fallback


def _make_send_kwargs(chat_id: int, text: str, reply_to: int | None) -> dict[str, Any]:
    """Build bot.send_message kwargs, adding reply_to_message_id when set."""
    kwargs: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        kwargs["reply_to_message_id"] = reply_to
    return kwargs


def _render_text(text: str) -> list[str]:
    """Convert Markdown to MarkdownV2 and split into <=4096-char chunks."""
    return chunk_text(text, TELEGRAM_MAX_LENGTH, escape_fn=_convert_markdown)


def _render_buttons(buttons: list) -> InlineKeyboardMarkup | None:
    """Convert list[Button] to InlineKeyboardMarkup, or None if empty."""
    if not buttons:
        return None
    kb = [
        [
            InlineKeyboardButton(text=b.text, callback_data=b.callback_data)
            for b in buttons
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _validate_inbound(
    inbound: InboundMessage, caller: str
) -> tuple[int, int | None, int | None] | None:
    """Validate platform is Telegram and extract (chat_id, topic_id, message_id).

    Returns None on validation failure (logs error).
    """
    if inbound.platform != Platform.TELEGRAM.value:
        log.error("%s called with non-telegram message id=%s", caller, inbound.id)
        return None
    if not isinstance(inbound.platform_meta, TelegramMeta):
        log.error(
            "%s: platform_meta missing 'chat_id' for msg id=%s", caller, inbound.id
        )
        return None
    chat_id: int = inbound.platform_meta.chat_id
    topic_id: int | None = inbound.platform_meta.topic_id
    message_id: int | None = inbound.platform_meta.message_id
    return chat_id, topic_id, message_id
