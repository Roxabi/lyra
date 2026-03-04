"""RED-phase tests for Slice 2: Telegram adapter — aiogram v3 webhook.

All tests in this file are expected to FAIL until the GREEN phase implements:
  - src/lyra/adapters/telegram.py  (TelegramAdapter, app)
  - src/lyra/config.py             (load_config)
  - Message.from_adapter classmethod on lyra.core.message.Message

Tests are structured so they are collected by pytest without syntax errors,
but raise ImportError / AttributeError at runtime (not at collection time).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.message import DiscordContext

# ---------------------------------------------------------------------------
# T2 — Missing secret token → HTTP 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_secret_returns_401() -> None:
    """POST /webhooks/telegram/main without X-Telegram-Bot-Api-Secret-Token → 401."""
    import httpx

    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=adapter.app)
    ) as client:
        response = await client.post(
            "/webhooks/telegram/main",
            json={"update_id": 1},
        )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# T3 — _normalize() builds correct TelegramContext for private chat
# ---------------------------------------------------------------------------


def test_normalize_private_chat_context() -> None:
    """_normalize() on a private-chat message produces correct TelegramContext."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED
    from lyra.core.message import Platform, TelegramContext

    hub = MagicMock()
    adapter = TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
    )

    msg = adapter._normalize(aiogram_msg)

    assert msg.platform == Platform.TELEGRAM
    expected_ctx = TelegramContext(chat_id=123, topic_id=None, is_group=False)
    assert msg.platform_context == expected_ctx


# ---------------------------------------------------------------------------
# T4 — is_mention logic
# ---------------------------------------------------------------------------


def test_is_mention_false_in_private_chat() -> None:
    """Private chat → is_mention=False regardless of entities."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
    )

    msg = adapter._normalize(aiogram_msg)

    assert msg.is_mention is False


def test_is_mention_true_when_entity_at_offset_zero() -> None:
    """Group chat with @mention entity at offset 0 matching bot username → True."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)

    entity = SimpleNamespace(type="mention", offset=0, length=9)  # "@lyra_bot"
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=456, type="group"),
        from_user=SimpleNamespace(id=42, full_name="Bob", is_bot=False),
        text="@lyra_bot hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=[entity],
    )

    msg = adapter._normalize(aiogram_msg)

    assert msg.is_mention is True


# ---------------------------------------------------------------------------
# T5 — Message.from_adapter() hardcodes trust="user"
# ---------------------------------------------------------------------------


def test_from_adapter_hardcodes_trust() -> None:
    """Message.from_adapter always produces trust='user'."""
    # AttributeError expected in RED — from_adapter does not exist yet
    from lyra.core.message import (
        Message,
        MessageType,
        Platform,
        TelegramContext,
        TextContent,
    )

    msg = Message.from_adapter(
        platform=Platform.TELEGRAM,
        bot_id="main",
        user_id="tg:user:42",
        user_name="Alice",
        content=TextContent(text="hi"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        is_mention=False,
        is_from_bot=False,
        platform_context=TelegramContext(chat_id=123),
    )

    assert msg.trust == "user"


# ---------------------------------------------------------------------------
# T6 — Backpressure: bus full → send ack before putting to bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backpressure_sends_ack_when_bus_full() -> None:
    """When hub.bus.full() is True, _on_message sends an ack before enqueuing."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    hub.bus = MagicMock()
    hub.bus.full.return_value = True
    hub.bus.put = AsyncMock()

    bot = AsyncMock()
    bot.get_me = AsyncMock(return_value=SimpleNamespace(username="lyra_bot"))

    adapter = TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)
    adapter.bot = bot

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
        message_id=1,
    )

    await adapter._on_message(aiogram_msg)

    bot.send_message.assert_called_once()
    call_kwargs = bot.send_message.call_args
    assert call_kwargs is not None


# ---------------------------------------------------------------------------
# T7 — send() calls bot.send_message(chat_id, text)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_calls_bot_send_message() -> None:
    """adapter.send(hub_msg, Response) calls bot.send_message(chat_id=..., text=...)."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED
    from lyra.core.message import (
        Message,
        MessageType,
        Platform,
        Response,
        TelegramContext,
        TextContent,
    )

    hub = MagicMock()
    bot = AsyncMock()

    adapter = TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)
    adapter.bot = bot

    original_msg = Message(
        id="msg-1",
        platform=Platform.TELEGRAM,
        bot_id="main",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=TelegramContext(chat_id=123),
    )
    response = Response(content="reply")

    await adapter.send(original_msg, response)

    bot.send_message.assert_awaited_once_with(chat_id=123, text="reply")


# ---------------------------------------------------------------------------
# T8 — Bot token must not appear in log output
# ---------------------------------------------------------------------------


def test_token_not_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    """After _normalize(), no log record contains the bot token string."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
    )

    with caplog.at_level(logging.DEBUG, logger="lyra.adapters.telegram"):
        adapter._normalize(aiogram_msg)

    for record in caplog.records:
        assert "test-token-secret" not in record.getMessage()


# ---------------------------------------------------------------------------
# T9 — Missing TELEGRAM_TOKEN env var → SystemExit
# ---------------------------------------------------------------------------


def test_missing_token_raises_on_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_config() raises SystemExit with 'TELEGRAM_TOKEN' when env var is absent."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)

    from lyra.config import load_config  # ImportError expected in RED

    with pytest.raises(SystemExit, match="TELEGRAM_TOKEN"):
        load_config()


# ---------------------------------------------------------------------------
# T10 — send() with wrong platform_context type is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_skips_when_platform_context_is_not_telegram(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """adapter.send() with a non-TelegramContext platform_context must not call
    bot.send_message."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED
    from lyra.core.message import Message, MessageType, Platform, Response, TextContent

    hub = MagicMock()
    bot = AsyncMock()

    adapter = TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)
    adapter.bot = bot

    original_msg = Message(
        id="msg-discord",
        platform=Platform.TELEGRAM,
        bot_id="main",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=DiscordContext(guild_id=None, channel_id=123, message_id=456),
    )

    with caplog.at_level(logging.WARNING, logger="lyra.adapters.telegram"):
        await adapter.send(original_msg, Response(content="hi"))

    bot.send_message.assert_not_awaited()
    assert any("non-TelegramContext" in r.message for r in caplog.records)
