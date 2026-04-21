"""Tests for TelegramAdapter _typing_loop and typing-cancellation.

Covers send/send_streaming typing: T1.4, T1.5.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.render_events import TextRenderEvent

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
    from lyra.core.messaging.message import InboundMessage, OutboundMessage

    # Arrange
    bot = AsyncMock()
    sent_mock = MagicMock()
    sent_mock.message_id = 1
    bot.send_message = AsyncMock(return_value=sent_mock)

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=MagicMock(),
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

    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.messaging.message import InboundMessage

    # Arrange
    bot = AsyncMock()
    placeholder_mock = MagicMock()
    placeholder_mock.message_id = 10
    bot.send_message = AsyncMock(return_value=placeholder_mock)

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=MagicMock(),
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

    async def _chunks() -> AsyncIterator[TextRenderEvent]:
        yield TextRenderEvent(text="hello", is_final=True)

    # Act
    await adapter.send_streaming(original_msg, _chunks())

    # Assert — typing task was cancelled when the placeholder was sent.
    mock_task.cancel.assert_called_once()
    assert 456 not in adapter._typing_tasks
