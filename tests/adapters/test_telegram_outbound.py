"""Tests for TelegramAdapter outbound message rendering (Slice 3).

Covers: TestTelegramOutboundMessage — OutboundMessage send, _render_text,
_render_buttons, chunk splitting, reply metadata, and typing-task cancellation.
Also covers top-level send tests: T7, T10, T12, SC-13.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.telegram import _ALLOW_ALL
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.message import (  # noqa: F401
    Attachment,
    Button,
    CodeBlock,
    InboundMessage,
    OutboundMessage,
)
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# File-local helpers
# ---------------------------------------------------------------------------


def _make_telegram_adapter():
    """Build a TelegramAdapter with a MagicMock hub (no bot attached)."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    return adapter


def _make_telegram_message():
    """Build a minimal InboundMessage for adapter.send() calls."""
    return InboundMessage(
        id="msg-tg-138",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={"chat_id": 123, "message_id": 1},
        trust_level=TrustLevel.TRUSTED,
    )


def _make_open_registry(service: str) -> CircuitRegistry:
    """Build a CircuitRegistry with the named circuit tripped OPEN."""
    registry = CircuitRegistry()
    for name in ("anthropic", "telegram", "discord", "hub"):
        cb = CircuitBreaker(name, failure_threshold=1, recovery_timeout=60)
        if name == service:
            cb.record_failure()  # trips to OPEN
        registry.register(cb)
    return registry


# ---------------------------------------------------------------------------
# T7 — send() calls bot.send_message(chat_id, text)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_calls_bot_send_message() -> None:
    """adapter.send(hub_msg, OutboundMessage) calls bot.send_message.

    Verifies chat_id and text are passed correctly.
    """
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    bot = AsyncMock()

    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    adapter.bot = bot

    original_msg = InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 123,
            "topic_id": None,
            "message_id": 99,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )
    outbound = OutboundMessage.from_text("reply")

    await adapter.send(original_msg, outbound)

    bot.send_message.assert_awaited_once_with(
        chat_id=123,
        text="reply",
        parse_mode="MarkdownV2",
        reply_to_message_id=99,
    )


# ---------------------------------------------------------------------------
# T10 — send() with wrong platform_context type is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_skips_when_platform_context_is_not_telegram(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """adapter.send() with a non-telegram platform InboundMessage must not call
    bot.send_message."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    bot = AsyncMock()

    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    adapter.bot = bot

    original_msg = InboundMessage(
        id="msg-discord",
        platform="discord",
        bot_id="main",
        scope_id="channel:123",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "guild_id": None,
            "channel_id": 123,
            "message_id": 456,
            "thread_id": None,
            "channel_type": "text",
        },
        trust_level=TrustLevel.TRUSTED,
    )

    with caplog.at_level(logging.WARNING, logger="lyra.adapters.telegram"):
        await adapter.send(original_msg, OutboundMessage.from_text("hi"))

    bot.send_message.assert_not_awaited()
    assert any("non-telegram" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# T12 — send() stores bot's reply message_id in response.metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_stores_reply_message_id_in_metadata() -> None:
    """adapter.send() stores bot reply message_id in outbound.metadata."""
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    hub = MagicMock()
    bot = AsyncMock()
    sent_msg = SimpleNamespace(message_id=888)
    bot.send_message.return_value = sent_msg

    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    adapter.bot = bot

    original_msg = InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 123,
            "topic_id": None,
            "message_id": 777,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )
    outbound = OutboundMessage.from_text("reply")

    # Act
    await adapter.send(original_msg, outbound)

    # Assert
    bot.send_message.assert_awaited_once_with(
        chat_id=123,
        text="reply",
        parse_mode="MarkdownV2",
        reply_to_message_id=777,
    )
    assert outbound.metadata["reply_message_id"] == 888


# ---------------------------------------------------------------------------
# SC-13 — send() always delivers regardless of circuit state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_always_delivers_regardless_of_circuit_state() -> None:
    """SC-13 (updated): adapter.send() no longer checks the circuit breaker.
    CB check is owned by OutboundDispatcher. Adapter always delivers.
    """
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange — circuit is OPEN but adapter should still send (CB check in dispatcher)
    registry = _make_open_registry("telegram")

    hub = MagicMock()
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=99))

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        circuit_registry=registry,
    )
    adapter.bot = bot

    original_msg = InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 123,
            "topic_id": None,
            "message_id": 1,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )

    # Act
    await adapter.send(original_msg, OutboundMessage.from_text("reply"))

    # Assert — CB is open but adapter still sends (CB check owned by dispatcher)
    bot.send_message.assert_awaited_once()


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
        # Arrange
        adapter = _make_telegram_adapter()

        # Act
        chunks = adapter._render_text("")  # type: ignore[attr-defined]

        # Assert
        assert chunks == []

    def test_render_text_escapes_markdownv2(self) -> None:
        # _render_text("hello_world") returns ["hello\\_world"] (underscore escaped).
        # Arrange
        adapter = _make_telegram_adapter()

        # Act
        chunks = adapter._render_text("hello_world")  # type: ignore[attr-defined]

        # Assert
        assert chunks == [r"hello\_world"]

    def test_render_text_no_escape_for_plain(self) -> None:
        """_render_text("hello world") returns ["hello world"] unchanged.

        No special chars means no escaping needed.
        """
        # Arrange
        adapter = _make_telegram_adapter()

        # Act
        chunks = adapter._render_text("hello world")  # type: ignore[attr-defined]

        # Assert
        assert chunks == ["hello world"]

    def test_render_text_chunks_at_4096(self) -> None:
        """_render_text("x" * 5000) returns 2 chunks, each ≤ 4096 characters."""
        # Arrange
        adapter = _make_telegram_adapter()
        text = "x" * 5000

        # Act
        chunks = adapter._render_text(text)  # type: ignore[attr-defined]

        # Assert
        assert len(chunks) == 2
        assert all(len(c) <= 4096 for c in chunks)

    def test_render_buttons_none_when_empty(self) -> None:
        """_render_buttons([]) returns None."""
        # Arrange
        adapter = _make_telegram_adapter()

        # Act
        result = adapter._render_buttons([])  # type: ignore[attr-defined]

        # Assert
        assert result is None

    def test_render_buttons_returns_keyboard(self) -> None:
        """_render_buttons([Button("Yes","yes")]) returns an InlineKeyboardMarkup."""
        from aiogram.types import InlineKeyboardMarkup  # ImportError if aiogram absent

        # Arrange
        adapter = _make_telegram_adapter()

        # Act
        result = adapter._render_buttons([Button("Yes", "yes")])  # type: ignore[attr-defined]

        # Assert
        assert isinstance(result, InlineKeyboardMarkup)

    @pytest.mark.asyncio
    async def test_buttons_only_on_last_chunk(self) -> None:
        """Sending OutboundMessage with long content + buttons: first bot.send_message
        call has no reply_markup, second (last) call has reply_markup."""
        # Arrange
        adapter = _make_telegram_adapter()

        calls: list[dict] = []

        async def capture_send(**kwargs):  # type: ignore[return]
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

        # Assert — two send calls were made (5000 chars → 2 chunks of ≤ 4096)
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
