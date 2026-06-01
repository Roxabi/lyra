"""Tests for TelegramAdapter OutboundMessage rendering (Slice 3).

Covers: TestTelegramOutboundMessage — _render_text, _render_buttons,
chunk splitting, reply metadata, and button placement.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.telegram.telegram_formatting import (
    _render_buttons as render_buttons,
)
from lyra.adapters.telegram.telegram_formatting import (
    _render_text as render_text,
)
from lyra.core.messaging.message import (  # noqa: F401
    Button,
    OutboundMessage,
)
from tests.adapters.conftest import _make_telegram_adapter, _make_telegram_message

# ---------------------------------------------------------------------------
# Slice 3 RED tests — TelegramAdapter rendering of OutboundMessage
# ---------------------------------------------------------------------------


class TestTelegramOutboundMessage:
    """Slice 3 RED tests — TelegramAdapter rendering of OutboundMessage."""

    @pytest.mark.asyncio
    async def test_send_accepts_outbound_message(self) -> None:
        """adapter.send(msg, OutboundMessage.from_text("hello")) calls
        bot.send_message once with chat_id and text="hello"."""
        # Arrange
        adapter = _make_telegram_adapter()
        sent_mock = MagicMock()
        sent_mock.message_id = 42
        adapter.bot = AsyncMock()
        adapter.bot.send_message = AsyncMock(return_value=sent_mock)

        outbound = OutboundMessage.from_text("hello")
        original_msg = _make_telegram_message()

        # Act
        await adapter.send(original_msg, outbound)

        # Assert
        adapter.bot.send_message.assert_awaited_once()
        call_kwargs = adapter.bot.send_message.call_args
        assert call_kwargs.kwargs.get("chat_id") == 123 or (
            len(call_kwargs.args) > 0 and call_kwargs.args[0] == 123
        )
        assert call_kwargs.kwargs.get("text") == "hello" or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] == "hello"
        )

    def test_render_text_empty_returns_no_chunks(self) -> None:
        """_render_text("") returns [] — no empty-string chunk to send to the API."""
        # Act
        chunks = render_text("")
        # Assert
        assert chunks == []

    def test_render_text_escapes_markdownv2(self) -> None:
        # _render_text("hello_world") returns ["hello\\_world"] (underscore escaped).
        # Act
        chunks = render_text("hello_world")
        # Assert
        assert chunks == [r"hello\_world"]

    def test_render_text_no_escape_for_plain(self) -> None:
        """_render_text("hello world") returns ["hello world"] unchanged.

        No special chars means no escaping needed.
        """
        # Act
        chunks = render_text("hello world")
        # Assert
        assert chunks == ["hello world"]

    def test_render_text_chunks_at_4096(self) -> None:
        """_render_text("x" * 5000) returns 2 chunks, each <= 4096 characters."""
        # Arrange
        text = "x" * 5000

        # Act
        chunks = render_text(text)
        # Assert
        assert len(chunks) == 2
        assert all(len(c) <= 4096 for c in chunks)

    def test_render_buttons_none_when_empty(self) -> None:
        """_render_buttons([]) returns None."""
        # Act
        result = render_buttons([])
        # Assert
        assert result is None

    def test_render_buttons_returns_keyboard(self) -> None:
        """_render_buttons([Button("Yes","yes")]) returns an InlineKeyboardMarkup."""
        from aiogram.types import InlineKeyboardMarkup  # ImportError if aiogram absent

        # Act
        result = render_buttons([Button("Yes", "yes")])
        # Assert
        assert isinstance(result, InlineKeyboardMarkup)

    @pytest.mark.asyncio
    async def test_buttons_only_on_last_chunk(self) -> None:
        """Sending OutboundMessage with long content + buttons: first bot.send_message
        call has no reply_markup, second (last) call has reply_markup."""
        # Arrange
        adapter = _make_telegram_adapter()

        calls: list[dict] = []

        async def capture_send(**kwargs: object) -> MagicMock:
            calls.append(dict(kwargs))
            m = MagicMock()
            m.message_id = len(calls)
            return m

        adapter.bot = AsyncMock()
        adapter.bot.send_message = capture_send

        outbound = OutboundMessage(
            content=["x" * 5000],
            buttons=[Button("Yes", "yes")],
        )
        original_msg = _make_telegram_message()

        # Act
        await adapter.send(original_msg, outbound)

        # Assert — two send calls were made (5000 chars -> 2 chunks of <= 4096)
        assert len(calls) == 2, f"Expected 2 send_message calls, got {len(calls)}"
        # First chunk: no reply_markup key, or reply_markup is None/falsy
        assert calls[0].get("reply_markup") is None or "reply_markup" not in calls[0]
        # Last chunk: reply_markup is set (truthy)
        assert calls[1].get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_reply_message_id_stored_in_metadata(self) -> None:
        """send() stores the reply message_id in outbound.metadata."""
        # Arrange
        adapter = _make_telegram_adapter()
        sent_mock = MagicMock()
        sent_mock.message_id = 999
        adapter.bot = AsyncMock()
        adapter.bot.send_message = AsyncMock(return_value=sent_mock)

        outbound = OutboundMessage.from_text("hi")
        original_msg = _make_telegram_message()

        # Act
        await adapter.send(original_msg, outbound)

        # Assert
        assert outbound.metadata.get("reply_message_id") == 999


@pytest.mark.asyncio
async def test_telegram_fallback_sets_reply_message_id() -> None:
    """When send_streaming fallback path is taken (placeholder fails),
    outbound.metadata['reply_message_id'] is set from the fallback message."""
    from tests.adapters.conftest import _make_telegram_adapter, _make_telegram_message

    adapter = _make_telegram_adapter()
    sent_mock = MagicMock()
    sent_mock.message_id = 77
    adapter.bot = AsyncMock()
    # Make send_message raise to trigger fallback
    adapter.bot.send_message = AsyncMock(
        side_effect=[Exception("placeholder failed"), sent_mock]
    )  # noqa: E501

    original_msg = _make_telegram_message()
    outbound = OutboundMessage.from_text("")

    async def _events():
        from lyra.core.messaging.render_events import (
            TextDeltaRenderEvent,
            TextEndRenderEvent,
        )

        yield TextDeltaRenderEvent(message_id="msg1", delta="hello")
        yield TextEndRenderEvent(message_id="msg1")

    await adapter.send_streaming(original_msg, _events(), outbound=outbound)
    assert outbound.metadata.get("reply_message_id") == 77
