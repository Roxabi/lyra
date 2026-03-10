"""Tests for Message.extract_scope_id() across all platform contexts.

RED phase — these tests exercise the new extract_scope_id() method which does
not exist yet. All tests are expected to FAIL until the GREEN phase implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from lyra.core.message import (
    DiscordContext,
    Message,
    MessageType,
    Platform,
    TelegramContext,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_message(
    platform: Platform = Platform.TELEGRAM,
    bot_id: str = "main",
    user_id: str = "alice",
    platform_context: TelegramContext | DiscordContext | None = None,
) -> Message:
    if platform_context is None:
        platform_context = TelegramContext(chat_id=42)
    return Message(
        id="msg-1",
        platform=platform,
        bot_id=bot_id,
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content="hello",
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=platform_context,
    )


# ---------------------------------------------------------------------------
# TestExtractScopeId
# ---------------------------------------------------------------------------


class TestExtractScopeId:
    """extract_scope_id() returns the canonical scope string for each context type."""

    def test_telegram_dm_returns_chat_scope(self) -> None:
        # Arrange
        ctx = TelegramContext(chat_id=555)
        msg = make_message(platform=Platform.TELEGRAM, platform_context=ctx)
        # Act
        scope_id = msg.extract_scope_id()
        # Assert
        assert scope_id == "chat:555"

    def test_telegram_group_returns_chat_scope(self) -> None:
        # Arrange
        ctx = TelegramContext(chat_id=888, is_group=True)
        msg = make_message(platform=Platform.TELEGRAM, platform_context=ctx)
        # Act
        scope_id = msg.extract_scope_id()
        # Assert
        assert scope_id == "chat:888"

    def test_telegram_forum_topic_returns_chat_and_topic_scope(self) -> None:
        # Arrange
        ctx = TelegramContext(chat_id=888, topic_id=42)
        msg = make_message(platform=Platform.TELEGRAM, platform_context=ctx)
        # Act
        scope_id = msg.extract_scope_id()
        # Assert
        assert scope_id == "chat:888:topic:42"

    def test_discord_thread_returns_thread_scope(self) -> None:
        # Arrange
        ctx = DiscordContext(guild_id=1, channel_id=2, message_id=3, thread_id=111)
        msg = make_message(platform=Platform.DISCORD, platform_context=ctx)
        # Act
        scope_id = msg.extract_scope_id()
        # Assert
        assert scope_id == "thread:111"

    def test_discord_channel_returns_channel_scope(self) -> None:
        # Arrange
        ctx = DiscordContext(guild_id=1, channel_id=222, message_id=3)
        msg = make_message(platform=Platform.DISCORD, platform_context=ctx)
        # Act
        scope_id = msg.extract_scope_id()
        # Assert
        assert scope_id == "channel:222"
