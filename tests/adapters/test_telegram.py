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

from lyra.adapters.telegram import _ALLOW_ALL
from lyra.core.auth import TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.message import InboundMessage
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
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
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
    """normalize() on a private-chat message produces correct platform_meta."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
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

    msg = adapter.normalize(aiogram_msg)

    assert isinstance(msg, InboundMessage)
    assert msg.platform == "telegram"
    assert msg.scope_id == "chat:123"
    assert msg.text == "hello"
    assert msg.user_id == "tg:user:42"
    assert msg.platform_meta["chat_id"] == 123
    assert msg.platform_meta["topic_id"] is None
    assert msg.platform_meta["is_group"] is False
    assert msg.platform_meta["message_id"] == 99


# ---------------------------------------------------------------------------
# T4 — is_mention logic
# ---------------------------------------------------------------------------


def test_is_mention_false_in_private_chat() -> None:
    """Private chat → is_mention=False regardless of entities."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.is_mention is False


def test_is_mention_true_when_entity_at_offset_zero() -> None:
    """Group chat with @mention entity at offset 0 matching bot username → True."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
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

    msg = adapter.normalize(aiogram_msg)

    assert msg.is_mention is True


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
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
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
    """adapter.send(hub_msg, OutboundMessage) calls bot.send_message.

    Verifies chat_id and text are passed correctly.
    """
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED
    from lyra.core.message import InboundMessage, OutboundMessage

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
        chat_id=123, text="reply", parse_mode="MarkdownV2"
    )


# ---------------------------------------------------------------------------
# T8 — Bot token must not appear in log output
# ---------------------------------------------------------------------------


def test_token_not_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    """After _normalize(), no log record contains the bot token string."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
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
        adapter.normalize(aiogram_msg)

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
    """adapter.send() with a non-telegram platform InboundMessage must not call
    bot.send_message."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED
    from lyra.core.message import InboundMessage, OutboundMessage

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
# T11 — _normalize() captures message_id from incoming Telegram message
# ---------------------------------------------------------------------------


def test_normalize_captures_message_id() -> None:
    """normalize() captures message_id in platform_meta."""
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
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
    msg = adapter.normalize(aiogram_msg)

    # Assert
    assert isinstance(msg, InboundMessage)
    assert msg.platform_meta["message_id"] == 777


def test_normalize_message_id_none_when_absent() -> None:
    """normalize() sets platform_meta message_id=None when message_id absent.

    Note: real aiogram Message objects always have message_id (required Bot API field).
    This test exercises the getattr defensive fallback used by SimpleNamespace stubs.
    """
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
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
    msg = adapter.normalize(aiogram_msg)

    # Assert
    assert isinstance(msg, InboundMessage)
    assert msg.platform_meta["message_id"] is None


# ---------------------------------------------------------------------------
# T11c — _normalize() captures both topic_id and message_id for group/forum
# ---------------------------------------------------------------------------


def test_normalize_captures_topic_and_message_id_for_forum() -> None:
    """Forum supergroup: both topic_id and message_id captured simultaneously."""
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
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
    msg = adapter.normalize(aiogram_msg)

    # Assert
    assert isinstance(msg, InboundMessage)
    assert msg.platform_meta["topic_id"] == 99
    assert msg.platform_meta["message_id"] == 777
    assert msg.platform_meta["is_group"] is True
    assert msg.scope_id == "chat:456:topic:99"


# ---------------------------------------------------------------------------
# T12 — send() stores bot's reply message_id in response.metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_stores_reply_message_id_in_metadata() -> None:
    """adapter.send() stores bot reply message_id in outbound.metadata."""
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.message import InboundMessage, OutboundMessage

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
        chat_id=123, text="reply", parse_mode="MarkdownV2"
    )
    assert outbound.metadata["reply_message_id"] == 888


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
        circuit_registry=registry,
        auth=_ALLOW_ALL,
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
    from lyra.core.message import InboundMessage, OutboundMessage

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
        webhook_secret="secret",
        circuit_registry=registry,
        auth=_ALLOW_ALL,
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
        msg_manager=mm,
        auth=_ALLOW_ALL,
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


def test_normalize_empty_text() -> None:
    """normalize() with text=None produces msg.text == \"\"."""
    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text=None,
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=99,
        entities=None,
    )
    msg = adapter.normalize(aiogram_msg)
    assert msg.text == ""


@pytest.mark.asyncio
async def test_on_message_drops_bot_text_message() -> None:
    """_on_message drops messages when from_user.is_bot=True."""
    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    bot_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=99, full_name="BotUser", is_bot=True),
        text="I am a bot",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=1,
        entities=None,
    )
    await adapter._on_message(bot_msg)
    hub.inbound_bus.put.assert_not_called()


# ---------------------------------------------------------------------------
# RED — Slice 3: OutboundMessage render tests for TelegramAdapter (#138)
# ---------------------------------------------------------------------------

from lyra.core.message import (  # noqa: E402,F401 — Slice V2 green
    Attachment,
    Button,
    CodeBlock,
    OutboundMessage,
)


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
    from datetime import datetime, timezone

    from lyra.core.message import InboundMessage

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


# ---------------------------------------------------------------------------
# Inbound attachment extraction (#183)
# ---------------------------------------------------------------------------


class TestTelegramAttachments:
    """TelegramAdapter.normalize() extracts non-audio attachments."""

    def _make_adapter(self):
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        return TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)

    def _make_msg(  # noqa: PLR0913 — test factory with optional overrides
        self, *, text: str | None = "hello", caption=None,
        photo=None, document=None, video=None,
        animation=None, sticker=None,
    ):
        return SimpleNamespace(
            chat=SimpleNamespace(id=123, type="private"),
            from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
            text=text,
            caption=caption,
            date=datetime.now(timezone.utc),
            message_thread_id=None,
            message_id=99,
            entities=None,
            photo=photo,
            document=document,
            video=video,
            animation=animation,
            sticker=sticker,
        )

    def test_normalize_photo_attachment(self) -> None:
        """Photo → type='image', tg:file_id: prefix, image/jpeg."""
        adapter = self._make_adapter()
        photo = [
            SimpleNamespace(file_id="small123"),
            SimpleNamespace(file_id="large456"),
        ]
        msg = adapter.normalize(self._make_msg(photo=photo))
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "image"
        assert a.url_or_path_or_bytes == "tg:file_id:large456"
        assert a.mime_type == "image/jpeg"

    def test_normalize_photo_with_caption(self) -> None:
        """Photo with caption → caption in text, photo in attachments."""
        adapter = self._make_adapter()
        photo = [SimpleNamespace(file_id="pic123")]
        msg = adapter.normalize(
            self._make_msg(
                text=None, caption="Look at this!", photo=photo,
            ),
        )
        assert msg.text == "Look at this!"
        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "image"

    def test_normalize_document_attachment(self) -> None:
        """Document → type='file', correct mime_type and filename."""
        adapter = self._make_adapter()
        doc = SimpleNamespace(
            file_id="doc789",
            mime_type="application/pdf",
            file_name="report.pdf",
        )
        msg = adapter.normalize(self._make_msg(document=doc))
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "file"
        assert a.url_or_path_or_bytes == "tg:file_id:doc789"
        assert a.mime_type == "application/pdf"
        assert a.filename == "report.pdf"

    def test_normalize_video_attachment(self) -> None:
        """Video → type='video'."""
        adapter = self._make_adapter()
        vid = SimpleNamespace(file_id="vid101", mime_type="video/mp4")
        msg = adapter.normalize(self._make_msg(video=vid))
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "video"
        assert a.url_or_path_or_bytes == "tg:file_id:vid101"

    def test_normalize_animation_attachment(self) -> None:
        """Animation (GIF) → type='image', image/gif."""
        adapter = self._make_adapter()
        anim = SimpleNamespace(file_id="gif999")
        msg = adapter.normalize(
            self._make_msg(animation=anim),
        )
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "image"
        assert a.mime_type == "image/gif"
        assert a.url_or_path_or_bytes == "tg:file_id:gif999"

    def test_normalize_animated_sticker_skipped(self) -> None:
        """Animated sticker (is_animated=True) → NOT in attachments."""
        adapter = self._make_adapter()
        sticker = SimpleNamespace(file_id="stk1", is_animated=True, is_video=False)
        msg = adapter.normalize(self._make_msg(sticker=sticker))
        assert len(msg.attachments) == 0

    def test_normalize_video_sticker_skipped(self) -> None:
        """Video sticker (is_video=True) → NOT in attachments."""
        adapter = self._make_adapter()
        sticker = SimpleNamespace(
            file_id="stk3", is_animated=False, is_video=True,
        )
        msg = adapter.normalize(
            self._make_msg(sticker=sticker),
        )
        assert len(msg.attachments) == 0

    def test_normalize_static_sticker(self) -> None:
        """Static sticker → type='image', image/webp."""
        adapter = self._make_adapter()
        sticker = SimpleNamespace(file_id="stk2", is_animated=False, is_video=False)
        msg = adapter.normalize(self._make_msg(sticker=sticker))
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "image"
        assert a.mime_type == "image/webp"
        assert a.url_or_path_or_bytes == "tg:file_id:stk2"

    def test_normalize_text_only_empty_attachments(self) -> None:
        """Text-only message → empty attachments list."""
        adapter = self._make_adapter()
        msg = adapter.normalize(self._make_msg())
        assert msg.attachments == []


# ---------------------------------------------------------------------------
# Slice S4: TelegramAdapter auth gate tests
# ---------------------------------------------------------------------------


def _make_aiogram_msg(user_id: int = 42) -> object:
    """Build a minimal aiogram-like message SimpleNamespace."""
    from types import SimpleNamespace

    return SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=user_id, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=1,
        entities=None,
    )


class TestTelegramAuth:
    """Auth gate tests for TelegramAdapter._on_message and _on_voice_message."""

    @pytest.mark.asyncio
    async def test_blocked_user_skips_normalize(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """BLOCKED user: _on_message returns early without calling normalize()."""
        from unittest.mock import patch

        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.auth import AuthMiddleware, TrustLevel

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.BLOCKED

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)

        with caplog.at_level(logging.INFO, logger="lyra.adapters.telegram"):
            with patch.object(adapter, "normalize") as mock_norm:
                await adapter._on_message(_make_aiogram_msg())

        mock_norm.assert_not_called()
        hub.inbound_bus.put.assert_not_called()
        assert any("auth_reject" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_allowed_user_has_trust_level(self) -> None:
        """TRUSTED user: message produced with correct trust_level."""
        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.auth import AuthMiddleware, TrustLevel

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.TRUSTED

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)
        adapter.bot = AsyncMock()

        await adapter._on_message(_make_aiogram_msg())

        hub.inbound_bus.put.assert_called_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.trust_level == TrustLevel.TRUSTED

    @pytest.mark.asyncio
    async def test_voice_blocked_skips_normalize(self) -> None:
        """BLOCKED user on voice: _on_voice_message returns early without sending."""
        from unittest.mock import patch

        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.auth import AuthMiddleware, TrustLevel

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.BLOCKED

        hub = MagicMock()
        bot = AsyncMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)
        adapter.bot = bot

        voice_msg = _make_aiogram_msg()
        with patch.object(adapter, "normalize_audio") as mock_norm_audio:
            await adapter._on_voice_message(voice_msg)

        # bot.send_message should NOT have been called (blocked before handling)
        bot.send_message.assert_not_called()
        mock_norm_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_public_user_message_forwarded(self) -> None:
        """PUBLIC user: message reaches bus with trust_level=TrustLevel.PUBLIC."""
        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.auth import AuthMiddleware, TrustLevel

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.PUBLIC

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)
        adapter.bot = AsyncMock()

        await adapter._on_message(_make_aiogram_msg())

        hub.inbound_bus.put.assert_called_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.trust_level == TrustLevel.PUBLIC


# ---------------------------------------------------------------------------
# T1.4 — Unit tests for _typing_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typing_loop_sends_chat_action_immediately() -> None:
    """On entry, bot.send_chat_action called with (chat_id, "typing")."""

    from lyra.adapters.telegram import _typing_loop  # ImportError expected in RED

    bot = AsyncMock()
    chat_id = 123

    async with _typing_loop(bot, chat_id):
        pass

    bot.send_chat_action.assert_awaited_with(chat_id, "typing")


@pytest.mark.asyncio
async def test_typing_loop_refreshes_after_interval() -> None:
    """After interval elapses, send_chat_action called again."""
    import asyncio

    from lyra.adapters.telegram import _typing_loop  # ImportError expected in RED

    bot = AsyncMock()
    chat_id = 456

    async with _typing_loop(bot, chat_id, interval=0.05):
        await asyncio.sleep(0.12)  # enough time for at least one refresh

    # At least 2 calls: one on entry, at least one after interval
    assert bot.send_chat_action.await_count >= 2


@pytest.mark.asyncio
async def test_typing_loop_cancels_background_task_on_exit() -> None:
    """After context exits, no further send_chat_action calls are made."""
    import asyncio

    from lyra.adapters.telegram import _typing_loop  # ImportError expected in RED

    bot = AsyncMock()
    chat_id = 789

    async with _typing_loop(bot, chat_id, interval=0.05):
        pass  # exit immediately

    count_at_exit = bot.send_chat_action.await_count

    # Wait longer than interval to confirm no further calls after exit
    await asyncio.sleep(0.12)

    assert bot.send_chat_action.await_count == count_at_exit


@pytest.mark.asyncio
async def test_typing_loop_swallows_send_chat_action_exception() -> None:
    """If send_chat_action raises, no exception propagates out of the context."""
    from lyra.adapters.telegram import _typing_loop  # ImportError expected in RED

    bot = AsyncMock()
    bot.send_chat_action.side_effect = Exception("Telegram API error")
    chat_id = 111

    # Should not raise
    async with _typing_loop(bot, chat_id, interval=0.05):
        pass


@pytest.mark.asyncio
async def test_typing_loop_cancels_on_body_exception() -> None:
    """If body raises, finally still cancels the loop cleanly."""
    from lyra.adapters.telegram import _typing_loop  # ImportError expected in RED

    bot = AsyncMock()
    chat_id = 222

    with pytest.raises(ValueError, match="body error"):
        async with _typing_loop(bot, chat_id, interval=0.5):
            raise ValueError("body error")

    # No further calls should occur after the context exited (loop was cancelled)
    import asyncio
    count_after = bot.send_chat_action.await_count
    await asyncio.sleep(0.1)
    assert bot.send_chat_action.await_count == count_after


# ---------------------------------------------------------------------------
# T1.5 — Integration tests: send() and send_streaming() call send_chat_action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_calls_send_chat_action_typing() -> None:
    """adapter.send() calls bot.send_chat_action with (chat_id, "typing")."""
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.message import InboundMessage, OutboundMessage

    # Arrange
    hub = MagicMock()
    bot = AsyncMock()
    sent_mock = MagicMock()
    sent_mock.message_id = 1
    bot.send_message = AsyncMock(return_value=sent_mock)

    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    adapter.bot = bot

    original_msg = InboundMessage(
        id="msg-typing-1",
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

    # Act
    await adapter.send(original_msg, outbound)

    # Assert
    bot.send_chat_action.assert_awaited_with(123, "typing")


@pytest.mark.asyncio
async def test_send_streaming_calls_send_chat_action_typing() -> None:
    """adapter.send_streaming() calls bot.send_chat_action with (chat_id, "typing")."""
    from typing import AsyncIterator

    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.message import InboundMessage

    # Arrange
    hub = MagicMock()
    bot = AsyncMock()
    placeholder_mock = MagicMock()
    placeholder_mock.message_id = 10
    bot.send_message = AsyncMock(return_value=placeholder_mock)

    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    adapter.bot = bot

    original_msg = InboundMessage(
        id="msg-typing-stream-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:456",
        user_id="tg:user:99",
        user_name="Bob",
        is_mention=False,
        text="stream this",
        text_raw="stream this",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 456,
            "topic_id": None,
            "message_id": 10,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )

    async def _chunks() -> AsyncIterator[str]:
        yield "hello"

    # Act
    await adapter.send_streaming(original_msg, _chunks())

    # Assert
    bot.send_chat_action.assert_awaited_with(456, "typing")
