"""Shared test helpers for core tests."""

from __future__ import annotations

from datetime import datetime, timezone

from lyra.core.auth import TrustLevel
from lyra.core.message import (
    DiscordContext,
    Message,
    MessageType,
    Platform,
    TelegramContext,
)


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
        trust_level=TrustLevel.TRUSTED,
        platform_context=platform_context,
    )
