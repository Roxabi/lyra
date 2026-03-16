"""Telegram text formatting helpers.

Extracted from telegram.py (issue #297). All functions are stateless free
functions — no adapter instance required.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Any

from lyra.adapters._shared import (
    ATTACHMENT_EXTS_BASE,
    chunk_text,
    parse_reply_to_id,
    sanitize_filename,
    truncate_caption,
)
from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    Platform,
)

log = logging.getLogger(__name__)

# Telegram: base extensions + audio (Telegram supports audio via send_document).
_ATTACHMENT_EXTS = ATTACHMENT_EXTS_BASE | frozenset(
    {
        "ogg",
        "mp3",
        "opus",
        "wav",
        "flac",
        "aac",  # audio
    }
)

TELEGRAM_MAX_LENGTH = 4096  # Telegram Bot API text message limit

_MARKDOWNV2_SPECIAL = re.compile(r"([_*\[\]()~`>#\+\-=|{}.!\\])")

# Markdown → MarkdownV2 converter (preserves bold, italic, code, etc.)
try:
    from telegramify_markdown import markdownify as _md_to_mdv2

    def _convert_markdown(text: str) -> str:
        """Convert standard Markdown to Telegram MarkdownV2."""
        return _md_to_mdv2(text)

except ImportError:  # pragma: no cover — fallback if dependency missing
    log.warning("telegramify-markdown not installed; Telegram formatting disabled")

    def _convert_markdown(text: str) -> str:  # type: ignore[misc]
        return _MARKDOWNV2_SPECIAL.sub(r"\\\1", text)


def _make_send_kwargs(chat_id: int, text: str, reply_to: int | None) -> dict[str, Any]:
    """Build bot.send_message kwargs, adding reply_to_message_id when set."""
    kwargs: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        kwargs["reply_to_message_id"] = reply_to
    return kwargs


def _render_text(text: str) -> list[str]:
    """Convert Markdown to MarkdownV2 and split into <=4096-char chunks."""
    return chunk_text(
        text,
        TELEGRAM_MAX_LENGTH,
        escape_fn=_convert_markdown,
    )


def _render_buttons(buttons: list) -> object | None:
    """Convert list[Button] to InlineKeyboardMarkup, or None if empty."""
    if not buttons:
        return None
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = [
        [
            InlineKeyboardButton(text=b.text, callback_data=b.callback_data)
            for b in buttons
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def render_attachment(
    bot: Any, msg: OutboundAttachment, inbound: InboundMessage
) -> None:
    """Send an OutboundAttachment envelope via the appropriate Telegram method.

    Dispatches to send_photo, send_video, or send_document based on msg.type.
    Caption, reply_to, and topic threading follow the same pattern as render_audio.
    """
    if inbound.platform != Platform.TELEGRAM.value:
        log.error(
            "render_attachment() called with non-telegram message id=%s",
            inbound.id,
        )
        return

    chat_id: int | None = inbound.platform_meta.get("chat_id")
    if chat_id is None:
        log.error(
            "render_attachment: platform_meta missing 'chat_id' for msg id=%s",
            inbound.id,
        )
        return

    topic_id: int | None = inbound.platform_meta.get("topic_id")
    message_id: int | None = inbound.platform_meta.get("message_id")

    reply_to = parse_reply_to_id(msg.reply_to_id)
    if reply_to is None and message_id is not None:
        reply_to = message_id

    buf = BytesIO(msg.data)
    # Derive safe filename: sanitize explicit name or fallback from mime
    if msg.filename:
        buf.name = sanitize_filename(
            msg.filename,
            _ATTACHMENT_EXTS,
        )
    else:
        raw_ext = msg.mime_type.split("/")[-1] if "/" in msg.mime_type else ""
        ext = raw_ext if raw_ext in _ATTACHMENT_EXTS else "bin"
        buf.name = f"attachment.{ext}"

    kwargs: dict = {"chat_id": chat_id}
    if topic_id is not None:
        kwargs["message_thread_id"] = topic_id
    if reply_to is not None:
        kwargs["reply_to_message_id"] = reply_to
    truncated = truncate_caption(msg.caption, 1024)
    if truncated:
        kwargs["caption"] = truncated

    if msg.type == "image":
        kwargs["photo"] = buf
        await bot.send_photo(**kwargs)
    elif msg.type == "video":
        kwargs["video"] = buf
        await bot.send_video(**kwargs)
    else:
        # "document" and "file" both use send_document
        kwargs["document"] = buf
        await bot.send_document(**kwargs)
