"""Unit tests for TelegramFormatter tool recap in rich <tg-thinking> mode (#1952)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.adapters.telegram.telegram_formatter import TelegramFormatter
from tests.adapters.conftest import _make_telegram_adapter


def _make_trace(message_id: int = 777) -> MagicMock:
    trace = MagicMock()
    trace.message_id = message_id
    return trace


def _make_formatter(adapter) -> TelegramFormatter:
    return TelegramFormatter(
        adapter,
        chat_id=123,
        get_msg=lambda k, fb: fb,
        placeholder_text="…",
    )


@pytest.mark.asyncio
async def test_edit_tool_recap_rich_mode_uses_tg_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_TELEGRAM_RICH_MESSAGES", "1")
    adapter = _make_telegram_adapter()
    adapter.bot = MagicMock()
    adapter.bot.edit_message_text = AsyncMock()

    formatter = _make_formatter(adapter)
    lines = ["🔧 Done ✅", "✏️ `src/foo.py` (Edit)"]

    await formatter.edit_tool_recap(_make_trace(), lines, done=True)

    adapter.bot.edit_message_text.assert_awaited_once()
    rich = adapter.bot.edit_message_text.call_args.kwargs["rich_message"]
    assert rich.html is not None
    assert "<tg-thinking>" in rich.html
    assert "🔧 Done ✅" in rich.html
    assert "src/foo.py" in rich.html
    assert "*" not in rich.html


@pytest.mark.asyncio
async def test_send_trace_placeholder_rich_mode_uses_tg_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_TELEGRAM_RICH_MESSAGES", "1")
    adapter = _make_telegram_adapter()
    sent = MagicMock(message_id=888)
    adapter.bot = MagicMock()
    adapter.bot.send_rich_message = AsyncMock(return_value=sent)

    formatter = _make_formatter(adapter)
    trace_obj, message_id = await formatter.send_trace_placeholder()

    adapter.bot.send_rich_message.assert_awaited_once()
    rich = adapter.bot.send_rich_message.call_args.kwargs["rich_message"]
    assert rich.html is not None
    assert "<tg-thinking>" in rich.html
    assert "🔧 …" in rich.html
    assert trace_obj is sent
    assert message_id == 888


@pytest.mark.asyncio
async def test_edit_tool_recap_escapes_html_metacharacters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_TELEGRAM_RICH_MESSAGES", "1")
    adapter = _make_telegram_adapter()
    adapter.bot = MagicMock()
    adapter.bot.edit_message_text = AsyncMock()

    formatter = _make_formatter(adapter)
    lines = ["🔧 Working…", "💻 `<script>alert(1)</script>`"]

    await formatter.edit_tool_recap(_make_trace(), lines, done=False)

    rich = adapter.bot.edit_message_text.call_args.kwargs["rich_message"]
    assert "<script>" not in rich.html
    assert "&lt;script&gt;" in rich.html
