"""Tests for Message.extract_scope_id() across all platform contexts."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from lyra.core.message import (
    DiscordContext,
    Platform,
    TelegramContext,
)
from tests.core.conftest import make_message

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

    def test_unknown_context_raises_value_error(self) -> None:
        @dataclass(frozen=True)
        class UnknownContext:
            pass

        msg = make_message(platform_context=UnknownContext())  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Unknown platform context type"):
            msg.extract_scope_id()

    def test_telegram_topic_id_zero_is_not_none(self) -> None:
        """topic_id=0 must produce 'chat:N:topic:0', not 'chat:N'."""
        ctx = TelegramContext(chat_id=1, topic_id=0)
        msg = make_message(platform=Platform.TELEGRAM, platform_context=ctx)
        assert msg.extract_scope_id() == "chat:1:topic:0"

    def test_telegram_chat_id_zero(self) -> None:
        ctx = TelegramContext(chat_id=0)
        msg = make_message(platform=Platform.TELEGRAM, platform_context=ctx)
        assert msg.extract_scope_id() == "chat:0"
