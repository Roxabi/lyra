"""Tests for TelegramAdapter.normalize(), mention logic, backpressure, typing loop,
and streaming/send typing-cancellation behaviour.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.telegram import _ALLOW_ALL
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.message import InboundMessage
from lyra.core.messages import MessageManager
from lyra.core.trust import TrustLevel

TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "config"
    / "messages.toml"
)

# ---------------------------------------------------------------------------
# Circuit breaker helpers (used by multiple tests in this file)
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
    adapter._bot_username = "lyra_bot"  # simulate resolve_identity()

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
    assert msg.text == "hello"  # @mention stripped


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


# ---------------------------------------------------------------------------
# reply_to_id extraction in normalize()
# ---------------------------------------------------------------------------


def test_normalize_sets_reply_to_id_when_reply_present() -> None:
    """normalize() sets reply_to_id from raw.reply_to_message.message_id."""
    from types import SimpleNamespace

    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    reply_msg = SimpleNamespace(message_id=77)
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="reply here",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=88,
        entities=None,
        reply_to_message=reply_msg,
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.reply_to_id == "77"


def test_normalize_reply_to_id_none_when_no_reply() -> None:
    """normalize() sets reply_to_id to None when raw.reply_to_message is absent."""
    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="no reply",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=88,
        entities=None,
        reply_to_message=None,
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.reply_to_id is None


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
# SC-11 — _on_message() drops silently when hub circuit is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_drops_and_notifies_when_hub_circuit_open() -> None:
    """SC-11: drops (no bus.put) and notifies user when hub circuit is OPEN."""
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    registry = _make_open_registry("hub")

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock()

    bot = AsyncMock()
    bot.get_me = AsyncMock(return_value=SimpleNamespace(username="lyra_bot"))
    bot.send_message = AsyncMock()

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

    # Assert — inbound_bus.put must NOT be called (message dropped)
    hub.inbound_bus.put.assert_not_called()
    # Assert — user receives a circuit-open notification
    bot.send_message.assert_called_once()
    call_kwargs = bot.send_message.call_args
    sent_text = (
        call_kwargs.args[1] if call_kwargs.args else call_kwargs.kwargs.get("text", "")
    )
    assert "temporarily" in sent_text.lower() or "overloaded" in sent_text.lower()


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

    bot.send_chat_action.assert_awaited_once_with(chat_id, "typing")


@pytest.mark.asyncio
async def test_typing_loop_refreshes_after_interval() -> None:
    """After interval elapses, send_chat_action called again."""
    import asyncio

    from lyra.adapters.telegram import _typing_loop  # ImportError expected in RED

    bot = AsyncMock()
    chat_id = 456
    interval = 0.05

    async with _typing_loop(bot, chat_id, interval=interval):
        await asyncio.sleep(interval * 5)  # enough time for at least one refresh

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
# T1.5 — Integration tests: send() and send_streaming() cancel typing tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_cancels_typing_task() -> None:
    """adapter.send() cancels the typing task started on message receipt.

    The typing indicator is a background task started by _start_typing() (called
    in _on_message). send() cancels it via _cancel_typing() before sending the reply.
    """
    import asyncio

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

    # Simulate message receipt: pre-populate a typing task for chat_id 123
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.done.return_value = False
    adapter._typing_tasks[123] = mock_task

    # Act
    await adapter.send(original_msg, outbound)

    # Assert — typing task was cancelled before reply was sent
    mock_task.cancel.assert_called_once()
    assert 123 not in adapter._typing_tasks

    # Assert — reply was sent
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_streaming_cancels_typing_task_after_placeholder() -> None:
    """adapter.send_streaming() cancels the pre-existing typing task once the
    placeholder message is sent (first visible content in the chat).

    The typing indicator is started by _start_typing() at message receipt
    (_on_message / _on_voice_message). send_streaming() itself no longer
    creates a new typing loop — it only cancels the pre-existing task.
    """
    import asyncio
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

    # Simulate a typing task started by _start_typing() at message receipt.
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.done.return_value = False
    adapter._typing_tasks[456] = mock_task

    async def _chunks() -> AsyncIterator[str]:
        yield "hello"

    # Act
    await adapter.send_streaming(original_msg, _chunks())

    # Assert — typing task was cancelled when the placeholder was sent.
    mock_task.cancel.assert_called_once()
    assert 456 not in adapter._typing_tasks
