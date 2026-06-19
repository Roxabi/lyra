"""Telegram Rich Messages (Bot API 10.1+) — send helpers with MarkdownV2 fallback."""

from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from typing import Any

from aiogram.types import InputRichMessage

from factory.adapters.shared._shared import chunk_text
from factory.adapters.telegram.telegram_formatting import (
    TELEGRAM_MAX_LENGTH,
    _render_text,
)

log = logging.getLogger("factory.adapters.telegram")

_THINKING_TAG = "tg-thinking"


def rich_messages_enabled() -> bool:
    """Rich Messages on by default; set FACTORY_TELEGRAM_RICH_MESSAGES=0 to disable."""
    return os.environ.get("FACTORY_TELEGRAM_RICH_MESSAGES", "1").lower() not in (
        "0",
        "false",
        "no",
    )


def rich_drafts_enabled() -> bool:
    """Draft streaming on by default; set FACTORY_TELEGRAM_RICH_DRAFTS=0 to disable."""
    return os.environ.get("FACTORY_TELEGRAM_RICH_DRAFTS", "1").lower() not in (
        "0",
        "false",
        "no",
    )


def is_private_chat(chat_id: int) -> bool:
    return chat_id > 0


def use_rich_draft(chat_id: int) -> bool:
    return (
        rich_messages_enabled()
        and rich_drafts_enabled()
        and is_private_chat(chat_id)
    )


def build_rich_message(text: str) -> InputRichMessage:
    return InputRichMessage(markdown=text)


def build_thinking_message(text: str) -> InputRichMessage:
    """RichMessage thinking block — HTML <tg-thinking> (draft-only block type)."""
    safe = html.escape(text, quote=True)
    return InputRichMessage(html=f"<{_THINKING_TAG}>{safe}</{_THINKING_TAG}>")


def chunk_markdown(text: str) -> list[str]:
    """Split markdown without MarkdownV2 escaping (for sendRichMessage)."""
    return chunk_text(text, TELEGRAM_MAX_LENGTH, escape_fn=lambda s: s)


@dataclass
class TelegramPlaceholder:
    """Streaming placeholder — persisted message or private-chat draft."""

    chat_id: int
    topic_id: int | None
    message_id: int | None = None
    draft_id: int | None = None
    use_draft: bool = False


def _thread_kwargs(
    reply_to: int | None, topic_id: int | None
) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if reply_to is not None:
        kw["reply_to_message_id"] = reply_to
    if topic_id is not None:
        kw["message_thread_id"] = topic_id
    return kw


def resolve_draft_id(reply_to: int | None, chat_id: int) -> int:
    """Non-zero draft id required by sendRichMessageDraft."""
    if reply_to is not None:
        return reply_to
    return (chat_id % 2_147_483_000) or 1


async def send_rich_text(  # noqa: PLR0913 — mirrors Telegram send kwargs surface
    bot: Any,
    chat_id: int,
    text: str,
    *,
    reply_to: int | None = None,
    topic_id: int | None = None,
    reply_markup: Any = None,
) -> Any | None:
    """Send via sendRichMessage; return Message on success, None to signal fallback."""
    if not rich_messages_enabled() or not text:
        return None
    try:
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "rich_message": build_rich_message(text),
            **_thread_kwargs(reply_to, topic_id),
        }
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        return await bot.send_rich_message(**kwargs)
    except Exception as exc:  # noqa: BLE001 — rich path is best-effort; MarkdownV2 fallback follows
        log.debug("sendRichMessage failed, will fallback: type=%s", type(exc).__name__)
        return None


async def edit_rich_text(
    bot: Any,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: Any = None,
) -> bool:
    """Edit via editMessageText(rich_message=...); return True on success."""
    if not rich_messages_enabled() or not text:
        return False
    try:
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "rich_message": build_rich_message(text),
        }
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        await bot.edit_message_text(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 — rich edit is best-effort; MarkdownV2 fallback follows
        log.debug("editMessageText(rich_message) failed: type=%s", type(exc).__name__)
        return False


async def send_rich_draft(
    bot: Any,
    chat_id: int,
    draft_id: int,
    rich_message: InputRichMessage,
    *,
    topic_id: int | None = None,
) -> bool:
    """Stream a partial rich message in private chats (sendRichMessageDraft)."""
    if not use_rich_draft(chat_id):
        return False
    try:
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "draft_id": draft_id,
            "rich_message": rich_message,
        }
        if topic_id is not None:
            kwargs["message_thread_id"] = topic_id
        await bot.send_rich_message_draft(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 — draft API optional; caller may fall back
        log.debug("sendRichMessageDraft failed: type=%s", type(exc).__name__)
        return False


async def send_markdownv2_text(  # noqa: PLR0913 — mirrors Telegram send kwargs surface
    bot: Any,
    chat_id: int,
    text: str,
    *,
    reply_to: int | None = None,
    topic_id: int | None = None,
    reply_markup: Any = None,
) -> Any:
    """Legacy sendMessage path with MarkdownV2 escaping."""
    rendered = _render_text(text)
    if not rendered:
        rendered = [text]
    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "text": rendered[0],
        "parse_mode": "MarkdownV2",
        **_thread_kwargs(reply_to, topic_id),
    }
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    return await bot.send_message(**kwargs)


async def edit_markdownv2_text(
    bot: Any,
    chat_id: int,
    message_id: int,
    text: str,
) -> None:
    rendered = _render_text(text)
    if not rendered:
        return
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=rendered[0],
        parse_mode="MarkdownV2",
    )


async def send_text_with_fallback(  # noqa: PLR0913 — mirrors Telegram send kwargs surface
    bot: Any,
    chat_id: int,
    text: str,
    *,
    reply_to: int | None = None,
    topic_id: int | None = None,
    reply_markup: Any = None,
) -> Any:
    """Prefer sendRichMessage; fall back to sendMessage MarkdownV2."""
    sent = await send_rich_text(
        bot,
        chat_id,
        text,
        reply_to=reply_to,
        topic_id=topic_id,
        reply_markup=reply_markup,
    )
    if sent is not None:
        return sent
    return await send_markdownv2_text(
        bot,
        chat_id,
        text,
        reply_to=reply_to,
        topic_id=topic_id,
        reply_markup=reply_markup,
    )


async def edit_text_with_fallback(
    bot: Any,
    chat_id: int,
    message_id: int,
    text: str,
) -> None:
    """Prefer editMessageText(rich_message); fall back to MarkdownV2."""
    if await edit_rich_text(bot, chat_id, message_id, text):
        return
    await edit_markdownv2_text(bot, chat_id, message_id, text)