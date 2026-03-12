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
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth import AuthMiddleware, TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.message import DiscordContext
from lyra.core.messages import MessageManager

TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "config"
    / "messages.toml"
)

# ---------------------------------------------------------------------------
# T2 — Missing secret token → HTTP 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_secret_returns_401() -> None:
    """POST /webhooks/telegram/main without X-Telegram-Bot-Api-Secret-Token → 401."""
    import httpx

    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )

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
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=99,
        entities=None,
    )

    msg = adapter._normalize(aiogram_msg, trust_level=TrustLevel.TRUSTED)

    assert msg.platform == Platform.TELEGRAM
    expected_ctx = TelegramContext(
        chat_id=123, topic_id=None, is_group=False, message_id=99
    )
    assert msg.platform_context == expected_ctx


# ---------------------------------------------------------------------------
# T4 — is_mention logic
# ---------------------------------------------------------------------------


def test_is_mention_false_in_private_chat() -> None:
    """Private chat → is_mention=False regardless of entities."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
    )

    msg = adapter._normalize(aiogram_msg, trust_level=TrustLevel.TRUSTED)

    assert msg.is_mention is False


def test_is_mention_true_when_entity_at_offset_zero() -> None:
    """Group chat with @mention entity at offset 0 matching bot username → True."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )

    entity = SimpleNamespace(type="mention", offset=0, length=9)  # "@lyra_bot"
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=456, type="group"),
        from_user=SimpleNamespace(id=42, full_name="Bob", is_bot=False),
        text="@lyra_bot hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=[entity],
    )

    msg = adapter._normalize(aiogram_msg, trust_level=TrustLevel.TRUSTED)

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
        trust_level=TrustLevel.TRUSTED,
        platform_context=TelegramContext(chat_id=123),
    )

    assert msg.trust == "user"


# ---------------------------------------------------------------------------
# T6 — Backpressure: bus full → send ack before putting to bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backpressure_sends_ack_when_bus_full() -> None:
    """When put_nowait raises QueueFull, _on_message sends an ack."""
    import asyncio

    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock(side_effect=asyncio.QueueFull())

    bot = AsyncMock()
    bot.get_me = AsyncMock(return_value=SimpleNamespace(username="lyra_bot"))

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )
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

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )
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
        trust_level=TrustLevel.TRUSTED,
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
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
    )

    with caplog.at_level(logging.DEBUG, logger="lyra.adapters.telegram"):
        adapter._normalize(aiogram_msg, trust_level=TrustLevel.TRUSTED)

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

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )
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
        trust_level=TrustLevel.TRUSTED,
        platform_context=DiscordContext(guild_id=None, channel_id=123, message_id=456),
    )

    with caplog.at_level(logging.WARNING, logger="lyra.adapters.telegram"):
        await adapter.send(original_msg, Response(content="hi"))

    bot.send_message.assert_not_awaited()
    assert any("non-TelegramContext" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# T11 — _normalize() captures message_id from incoming Telegram message
# ---------------------------------------------------------------------------


def test_normalize_captures_message_id() -> None:
    """_normalize() sets TelegramContext.message_id from incoming aiogram message."""
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.message import TelegramContext

    # Arrange
    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=777,
        entities=None,
    )

    # Act
    msg = adapter._normalize(aiogram_msg, trust_level=TrustLevel.TRUSTED)

    # Assert
    assert isinstance(msg.platform_context, TelegramContext)
    assert msg.platform_context.message_id == 777


def test_normalize_message_id_none_when_absent() -> None:
    """_normalize() sets TelegramContext.message_id=None when message_id absent.

    Note: real aiogram Message objects always have message_id (required Bot API field).
    This test exercises the getattr defensive fallback used by SimpleNamespace stubs.
    """
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.message import TelegramContext

    # Arrange
    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        # no message_id attribute — exercises getattr(..., None) defensive path
        entities=None,
    )

    # Act
    msg = adapter._normalize(aiogram_msg, trust_level=TrustLevel.TRUSTED)

    # Assert
    assert isinstance(msg.platform_context, TelegramContext)
    assert msg.platform_context.message_id is None


# ---------------------------------------------------------------------------
# T11c — _normalize() captures both topic_id and message_id for group/forum
# ---------------------------------------------------------------------------


def test_normalize_captures_topic_and_message_id_for_forum() -> None:
    """Forum supergroup: both topic_id and message_id captured simultaneously."""
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.message import TelegramContext

    # Arrange
    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=456, type="supergroup"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello forum",
        date=datetime.now(timezone.utc),
        message_thread_id=99,
        message_id=777,
        entities=None,
    )

    # Act
    msg = adapter._normalize(aiogram_msg, trust_level=TrustLevel.TRUSTED)

    # Assert
    assert isinstance(msg.platform_context, TelegramContext)
    assert msg.platform_context.topic_id == 99
    assert msg.platform_context.message_id == 777
    assert msg.platform_context.is_group is True


# ---------------------------------------------------------------------------
# T12 — send() stores bot's reply message_id in response.metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_stores_reply_message_id_in_metadata() -> None:
    """adapter.send() stores bot reply message_id in response.metadata."""
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.message import (
        Message,
        MessageType,
        Platform,
        Response,
        TelegramContext,
        TextContent,
    )

    # Arrange
    hub = MagicMock()
    bot = AsyncMock()
    sent_msg = SimpleNamespace(message_id=888)
    bot.send_message.return_value = sent_msg

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
    )
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
        trust_level=TrustLevel.TRUSTED,
        platform_context=TelegramContext(chat_id=123, message_id=777),
    )
    response = Response(content="reply")

    # Act
    await adapter.send(original_msg, response)

    # Assert
    bot.send_message.assert_awaited_once_with(chat_id=123, text="reply")
    assert response.metadata["reply_message_id"] == 888


# ---------------------------------------------------------------------------
# Circuit breaker helpers
# ---------------------------------------------------------------------------


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
# SC-11 — _on_message() drops silently when hub circuit is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_drops_silently_when_hub_circuit_open() -> None:
    """SC-11: _on_message() drops silently (no bus.put) when circuits['hub'] is OPEN."""
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    registry = _make_open_registry("hub")

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock()

    bot = AsyncMock()
    bot.get_me = AsyncMock(return_value=SimpleNamespace(username="lyra_bot"))

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
        circuit_registry=registry,
    )
    adapter.bot = bot

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=1,
        entities=None,
    )

    # Act
    await adapter._on_message(aiogram_msg)

    # Assert — inbound_bus.put must NOT be called; message was silently dropped
    hub.inbound_bus.put.assert_not_called()


# ---------------------------------------------------------------------------
# SC-13 — send() skips bot.send_message when telegram circuit is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_always_delivers_regardless_of_circuit_state() -> None:
    """SC-13 (updated): adapter.send() no longer checks the circuit breaker.
    CB check is owned by OutboundDispatcher. Adapter always delivers.
    """
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.message import (
        Message,
        MessageType,
        Platform,
        Response,
        TelegramContext,
        TextContent,
    )

    # Arrange — circuit is OPEN but adapter should still send (CB check in dispatcher)
    registry = _make_open_registry("telegram")

    hub = MagicMock()
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=99))

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
        circuit_registry=registry,
    )
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
        trust_level=TrustLevel.TRUSTED,
        platform_context=TelegramContext(chat_id=123, message_id=1),
    )
    response = Response(content="reply")

    # Act
    await adapter.send(original_msg, response)

    # Assert — CB is open but adapter still sends (CB check owned by dispatcher)
    bot.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# SC-14 — GET /status returns all 4 circuit states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_endpoint_returns_all_circuits() -> None:
    """SC-14: GET /status → JSON with all 4 circuit states."""
    import httpx

    from lyra.adapters.telegram import TelegramAdapter

    # Arrange — registry with all 4 circuits
    registry = CircuitRegistry()
    for name in ("anthropic", "telegram", "discord", "hub"):
        registry.register(
            CircuitBreaker(name, failure_threshold=3, recovery_timeout=60)
        )

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
        webhook_secret="secret",
        circuit_registry=registry,
    )

    # Act
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=adapter.app)
    ) as client:
        response = await client.get(
            "/status",
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        )

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert "services" in data
    services = data["services"]
    for name in ("anthropic", "telegram", "discord", "hub"):
        assert name in services, f"Missing circuit '{name}' in /status response"
        assert "state" in services[name]


# ---------------------------------------------------------------------------
# msg_manager injection — backpressure_ack uses TOML string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_msg_manager_injection_backpressure_ack() -> None:
    """Injecting a real MessageManager causes _on_message to send the TOML
    'backpressure_ack' string (not the hardcoded fallback) when bus is full."""
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    mm = MessageManager(TOML_PATH)

    import asyncio

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock(side_effect=asyncio.QueueFull())

    bot = AsyncMock()
    bot.get_me = AsyncMock(return_value=SimpleNamespace(username="lyra_bot"))

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        auth=AuthMiddleware({}, TrustLevel.TRUSTED),
        msg_manager=mm,
    )
    adapter.bot = bot

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=1,
        entities=None,
    )

    # Act
    await adapter._on_message(aiogram_msg)

    # Assert — ack text matches the TOML value for telegram backpressure_ack
    expected = mm.get("backpressure_ack", platform="telegram")
    bot.send_message.assert_called_once()
    call_kwargs = bot.send_message.call_args
    assert call_kwargs.kwargs.get("text") == expected or (
        len(call_kwargs.args) > 1 and call_kwargs.args[1] == expected
    )
