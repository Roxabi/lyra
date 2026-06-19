"""Unit tests for telegram_rich helpers and private-chat draft streaming."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.adapters.telegram.telegram_formatter import TelegramFormatter
from factory.adapters.telegram.telegram_rich import (
    TelegramPlaceholder,
    build_rich_message,
    build_thinking_message,
    chunk_markdown,
    edit_text_with_fallback,
    send_text_with_fallback,
)
from tests.adapters.conftest import _make_telegram_adapter, wire_telegram_rich_bot


@pytest.mark.asyncio
async def test_send_text_with_fallback_uses_rich_message() -> None:
    bot = AsyncMock()
    wire_telegram_rich_bot(bot, message_id=7)

    await send_text_with_fallback(bot, 123, "hello_world")

    bot.send_rich_message.assert_awaited_once_with(
        chat_id=123,
        rich_message=build_rich_message("hello_world"),
    )
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_text_with_fallback_falls_back_to_markdownv2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_TELEGRAM_RICH_MESSAGES", "0")
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

    await send_text_with_fallback(bot, 123, "hello_world")

    bot.send_rich_message.assert_not_awaited()
    bot.send_message.assert_awaited_once()
    call_kwargs = bot.send_message.call_args.kwargs
    assert call_kwargs["parse_mode"] == "MarkdownV2"
    assert call_kwargs["text"] == r"hello\_world"


@pytest.mark.asyncio
async def test_send_text_with_fallback_on_rich_api_error() -> None:
    bot = AsyncMock()
    bot.send_rich_message = AsyncMock(side_effect=RuntimeError("rich API down"))
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=2))

    await send_text_with_fallback(bot, 123, "plain text")

    bot.send_rich_message.assert_awaited_once()
    bot.send_message.assert_awaited_once_with(
        chat_id=123,
        text="plain text",
        parse_mode="MarkdownV2",
    )


@pytest.mark.asyncio
async def test_edit_text_with_fallback_uses_rich_message() -> None:
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock(return_value=None)

    await edit_text_with_fallback(bot, 123, 55, "updated")

    bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=55,
        rich_message=build_rich_message("updated"),
    )


def test_chunk_markdown_does_not_escape_underscores() -> None:
    chunks = chunk_markdown("hello_world")
    assert chunks == ["hello_world"]


def test_build_rich_message_wraps_markdown() -> None:
    rich = build_rich_message("**bold**")
    assert rich.markdown == "**bold**"
    assert rich.html is None


def test_build_thinking_message_escapes_html() -> None:
    rich = build_thinking_message('</tg-thinking><b>x</b>')
    assert rich.html == "<tg-thinking>&lt;/tg-thinking&gt;&lt;b&gt;x&lt;/b&gt;</tg-thinking>"


@pytest.mark.asyncio
async def test_send_placeholder_draft_failure_falls_back_to_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_TELEGRAM_RICH_DRAFTS", "1")

    adapter = _make_telegram_adapter()
    bot = AsyncMock()
    sent = wire_telegram_rich_bot(bot, message_id=55)
    bot.send_rich_message_draft = AsyncMock(side_effect=RuntimeError("draft down"))
    adapter.bot = bot

    formatter = TelegramFormatter(
        adapter,
        chat_id=123,
        get_msg=lambda k, fb: fb,
        placeholder_text="…",
    )

    ph, reply_id = await formatter.send_placeholder()

    assert reply_id == sent.message_id
    assert ph.use_draft is False
    bot.send_rich_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_private_streaming_uses_draft_then_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Private chat + drafts enabled: draft placeholder, then persist on finalize."""
    monkeypatch.setenv("FACTORY_TELEGRAM_RICH_DRAFTS", "1")

    adapter = _make_telegram_adapter()
    bot = AsyncMock()
    sent = wire_telegram_rich_bot(bot, message_id=901)
    adapter.bot = bot

    formatter = TelegramFormatter(
        adapter,
        chat_id=123,
        get_msg=lambda k, fb: fb,
        placeholder_text="…",
        reply_to=77,
    )

    ph, reply_id = await formatter.send_placeholder()
    assert reply_id is None
    assert isinstance(ph, TelegramPlaceholder)
    assert ph.use_draft is True
    assert ph.draft_id == 77
    bot.send_rich_message_draft.assert_awaited_once()
    draft_kwargs = bot.send_rich_message_draft.call_args.kwargs
    assert draft_kwargs["chat_id"] == 123
    assert draft_kwargs["draft_id"] == 77
    assert draft_kwargs["rich_message"].html == "<tg-thinking>…</tg-thinking>"

    await formatter.edit_placeholder_text(ph, "partial", finalize=False)
    bot.send_rich_message_draft.assert_awaited()
    partial_kwargs = bot.send_rich_message_draft.call_args.kwargs
    assert partial_kwargs["rich_message"].markdown == "partial"

    await formatter.edit_placeholder_text(ph, "final answer", finalize=True)
    bot.send_rich_message.assert_awaited_once_with(
        chat_id=123,
        rich_message=build_rich_message("final answer"),
        reply_to_message_id=77,
    )
    assert ph.message_id == sent.message_id