"""Tests for TelegramAdapter outbound send() dispatch (Slice 3).

Covers top-level send tests: T7, T10, T12, SC-13.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (  # noqa: F401
    InboundMessage,
    OutboundMessage,
)

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
    for name in ("anthropic", "telegram", "discord", "hub"):
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
