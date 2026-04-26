"""Tests for TelegramAdapter outbound send() dispatch (Slice 3).

Covers top-level send tests: T7, T10, T12, SC-13, plus streaming callbacks,
tool summary formatting, and send edge cases (#932).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (  # noqa: F401
    InboundMessage,
    OutboundMessage,
)
from lyra.core.messaging.render_events import ToolSummaryRenderEvent
from tests.adapters.conftest import _make_telegram_adapter, _make_telegram_message

# ---------------------------------------------------------------------------
# T7 — send() calls bot.send_message(chat_id, text)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_calls_bot_send_message() -> None:
    """adapter.send(hub_msg, OutboundMessage) calls bot.send_message.

    Verifies chat_id and text are passed correctly.
    """
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    bot = AsyncMock()

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=MagicMock(),
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

    bot = AsyncMock()

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=MagicMock(),
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
    bot = AsyncMock()
    sent_msg = SimpleNamespace(message_id=888)
    bot.send_message.return_value = sent_msg

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=MagicMock(),
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
    from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry

    # Arrange — circuit is OPEN but adapter should still send (CB check in dispatcher)
    registry = CircuitRegistry()
    for name in ("claude-cli", "telegram", "discord", "hub"):
        cb = CircuitBreaker(name, failure_threshold=1, recovery_timeout=60)
        if name == "telegram":
            cb.record_failure()  # trips to OPEN
        registry.register(cb)

    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=99))

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=MagicMock(),
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
# Slice 1 — Streaming callbacks (T2)
# ---------------------------------------------------------------------------


def _make_discord_msg() -> InboundMessage:
    """Build a discord InboundMessage for noop/non-telegram tests."""
    return InboundMessage(
        id="msg-discord-noop",
        platform="discord",
        bot_id="main",
        scope_id="channel:99",
        user_id="dc:user:1",
        user_name="Bob",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "guild_id": 1,
            "channel_id": 99,
            "message_id": 55,
            "thread_id": None,
            "channel_type": "text",
        },
        trust_level=TrustLevel.TRUSTED,
    )


@pytest.mark.asyncio
async def test_build_streaming_noop_on_non_telegram_msg() -> None:
    """build_streaming_callbacks() with a non-telegram msg returns noop callbacks.

    Calling _send_placeholder on the noop result raises ValueError.
    Covers L176-182.
    """
    from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

    adapter = _make_telegram_adapter()
    adapter.bot = AsyncMock()
    original_msg = _make_discord_msg()
    outbound = OutboundMessage.from_text("hi")

    # Act
    callbacks = build_streaming_callbacks(adapter, original_msg, outbound)

    # Assert — noop send_placeholder raises ValueError
    with pytest.raises(ValueError, match="invalid inbound message"):
        await callbacks.send_placeholder()


@pytest.mark.asyncio
async def test_streaming_send_placeholder_with_reply() -> None:
    """_send_placeholder calls bot.send_message with reply_to_message_id
    when message_id is set. Covers L199-205.
    """
    from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

    adapter = _make_telegram_adapter()
    sent_mock = SimpleNamespace(message_id=42)
    adapter.bot = AsyncMock()
    adapter.bot.send_message = AsyncMock(return_value=sent_mock)

    original_msg = InboundMessage(
        id="msg-tg-reply",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 123,
            "message_id": 77,
            "topic_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )
    outbound = OutboundMessage.from_text("")

    callbacks = build_streaming_callbacks(adapter, original_msg, outbound)

    # Act
    await callbacks.send_placeholder()

    # Assert
    adapter.bot.send_message.assert_awaited_once()
    call_kwargs = adapter.bot.send_message.call_args.kwargs
    assert call_kwargs.get("reply_to_message_id") == 77


@pytest.mark.asyncio
async def test_streaming_send_placeholder_no_reply() -> None:
    """_send_placeholder sends without reply_to_message_id when message_id is None.
    Covers L199-205.
    """
    from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

    adapter = _make_telegram_adapter()
    sent_mock = SimpleNamespace(message_id=10)
    adapter.bot = AsyncMock()
    adapter.bot.send_message = AsyncMock(return_value=sent_mock)

    original_msg = InboundMessage(
        id="msg-tg-noreply",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 123,
            "message_id": None,
            "topic_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )
    outbound = OutboundMessage.from_text("")

    callbacks = build_streaming_callbacks(adapter, original_msg, outbound)

    # Act
    await callbacks.send_placeholder()

    # Assert — no reply_to_message_id kwarg
    call_kwargs = adapter.bot.send_message.call_args.kwargs
    assert "reply_to_message_id" not in call_kwargs


@pytest.mark.asyncio
async def test_streaming_edit_placeholder_text() -> None:
    """edit_placeholder_text closure calls bot.edit_message_text.

    Covers L208-218.
    """
    from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

    adapter = _make_telegram_adapter()
    adapter.bot = AsyncMock()
    adapter.bot.edit_message_text = AsyncMock()

    original_msg = _make_telegram_message()
    outbound = OutboundMessage.from_text("")

    callbacks = build_streaming_callbacks(adapter, original_msg, outbound)
    ph = SimpleNamespace(message_id=5)

    # Act
    await callbacks.edit_placeholder_text(ph, "hello")

    # Assert
    adapter.bot.edit_message_text.assert_awaited_once()
    call_kwargs = adapter.bot.edit_message_text.call_args.kwargs
    assert call_kwargs.get("message_id") == 5


@pytest.mark.asyncio
async def test_streaming_edit_placeholder_text_failure() -> None:
    """edit_placeholder_text logs debug on exception and does not re-raise.

    Covers L217-218.
    """
    from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

    adapter = _make_telegram_adapter()
    adapter.bot = AsyncMock()
    adapter.bot.edit_message_text = AsyncMock(side_effect=Exception("API error"))

    original_msg = _make_telegram_message()
    outbound = OutboundMessage.from_text("")

    callbacks = build_streaming_callbacks(adapter, original_msg, outbound)
    ph = SimpleNamespace(message_id=5)

    # Act — should not raise
    await callbacks.edit_placeholder_text(ph, "hello")

    # Assert — exception swallowed
    adapter.bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_edit_placeholder_tool() -> None:
    """edit_placeholder_tool closure formats tool summary + calls edit_message_text.
    Covers L221-232.
    """
    from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

    adapter = _make_telegram_adapter()
    adapter.bot = AsyncMock()
    adapter.bot.edit_message_text = AsyncMock()

    original_msg = _make_telegram_message()
    outbound = OutboundMessage.from_text("")

    callbacks = build_streaming_callbacks(adapter, original_msg, outbound)
    ph = SimpleNamespace(message_id=7)
    event = ToolSummaryRenderEvent(bash_commands=["ls"], is_complete=True)

    # Act
    await callbacks.edit_placeholder_tool(ph, event, "")

    # Assert
    adapter.bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_send_message() -> None:
    """send_message closure renders chunks and sends each, returning last message_id.
    Covers L235-244.
    """
    from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

    adapter = _make_telegram_adapter()
    sent_mock = SimpleNamespace(message_id=99)
    adapter.bot = AsyncMock()
    adapter.bot.send_message = AsyncMock(return_value=sent_mock)

    original_msg = _make_telegram_message()
    outbound = OutboundMessage.from_text("")

    callbacks = build_streaming_callbacks(adapter, original_msg, outbound)

    # Act
    result = await callbacks.send_message("hello world")

    # Assert
    adapter.bot.send_message.assert_awaited()
    assert result == 99


@pytest.mark.asyncio
async def test_streaming_send_fallback_with_text() -> None:
    """send_fallback with non-empty text renders and sends the message.

    Covers L246-256.
    """
    from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

    adapter = _make_telegram_adapter()
    sent_mock = SimpleNamespace(message_id=88)
    adapter.bot = AsyncMock()
    adapter.bot.send_message = AsyncMock(return_value=sent_mock)

    original_msg = _make_telegram_message()
    outbound = OutboundMessage.from_text("")

    callbacks = build_streaming_callbacks(adapter, original_msg, outbound)

    # Act
    result = await callbacks.send_fallback("fallback text")

    # Assert
    adapter.bot.send_message.assert_awaited()
    assert result == 88


@pytest.mark.asyncio
async def test_streaming_send_fallback_empty_text() -> None:
    """send_fallback with empty string uses placeholder_text.

    Covers L250.
    """
    from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

    adapter = _make_telegram_adapter()
    sent_mock = SimpleNamespace(message_id=77)
    adapter.bot = AsyncMock()
    adapter.bot.send_message = AsyncMock(return_value=sent_mock)

    original_msg = _make_telegram_message()
    outbound = OutboundMessage.from_text("")

    callbacks = build_streaming_callbacks(adapter, original_msg, outbound)

    # Act — empty string triggers fallback to placeholder_text
    result = await callbacks.send_fallback("")

    # Assert — send_message was called (with placeholder text as fallback)
    adapter.bot.send_message.assert_awaited()
    assert result == 77


# ---------------------------------------------------------------------------
# Slice 2 — Tool summary + send edges (T4)
# ---------------------------------------------------------------------------


def test_format_tool_summary_complete() -> None:
    """_format_tool_summary with is_complete=True returns 'Done' and checkmark.
    Covers L154-158.
    """
    from lyra.adapters.telegram.telegram_outbound import _format_tool_summary

    event = ToolSummaryRenderEvent(is_complete=True)

    result = _format_tool_summary(event)

    assert "Done" in result
    assert "✅" in result  # ✅


def test_format_tool_summary_incomplete() -> None:
    """_format_tool_summary with is_complete=False returns header with 'Working'.

    Covers L154-158.
    """
    from lyra.adapters.telegram.telegram_outbound import _format_tool_summary

    event = ToolSummaryRenderEvent(is_complete=False)

    result = _format_tool_summary(event)

    assert "Working" in result


@pytest.mark.asyncio
async def test_send_intermediate_starts_typing() -> None:
    """When outbound.intermediate=True, adapter._start_typing is called after send.

    Covers L147.
    """
    from lyra.adapters.telegram.telegram_outbound import send

    adapter = _make_telegram_adapter()
    sent_mock = SimpleNamespace(message_id=1)
    adapter.bot = AsyncMock()
    adapter.bot.send_message = AsyncMock(return_value=sent_mock)
    adapter._start_typing = MagicMock()
    adapter._cancel_typing = MagicMock()

    original_msg = _make_telegram_message()
    outbound = OutboundMessage.from_text("reply")
    outbound.intermediate = True

    # Act
    await send(adapter, original_msg, outbound)

    # Assert
    adapter._start_typing.assert_called_once()
    adapter._cancel_typing.assert_not_called()


@pytest.mark.asyncio
async def test_send_no_reply_to() -> None:
    """When message_id=None in platform_meta, reply_to_message_id not passed.
    Covers L139-140.
    """
    from lyra.adapters.telegram.telegram_outbound import send

    adapter = _make_telegram_adapter()
    sent_mock = SimpleNamespace(message_id=1)
    adapter.bot = AsyncMock()
    adapter.bot.send_message = AsyncMock(return_value=sent_mock)

    original_msg = InboundMessage(
        id="msg-no-reply",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 123,
            "message_id": None,
            "topic_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )
    outbound = OutboundMessage.from_text("reply")

    # Act
    await send(adapter, original_msg, outbound)

    # Assert — no reply_to_message_id kwarg
    call_kwargs = adapter.bot.send_message.call_args.kwargs
    assert "reply_to_message_id" not in call_kwargs


@pytest.mark.asyncio
async def test_typing_worker_bailout_after_3_failures() -> None:
    """_typing_worker stops after 3 consecutive send_chat_action failures.

    Covers L53-67.
    """
    from lyra.adapters.telegram.telegram_outbound import _typing_worker

    bot = AsyncMock()
    bot.send_chat_action = AsyncMock(side_effect=Exception("API error"))

    # Patch asyncio.sleep to avoid real delays
    sleep_target = "lyra.adapters.telegram.telegram_outbound.asyncio.sleep"
    with patch(sleep_target, new_callable=AsyncMock):
        await _typing_worker(bot, chat_id=123, interval=0.0)

    # Assert — exactly 3 failures caused a break (send_chat_action called 3 times)
    assert bot.send_chat_action.await_count == 3
