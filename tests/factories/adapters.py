"""Adapter factory helpers and fixtures for tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import DiscordMeta, InboundMessage, TelegramMeta

__all__ = [
    "attach_typing_cm",
    "discord_adapter",
    "make_dc_adapter",
    "make_dc_attach_msg",
    "make_dc_inbound_msg",
    "make_dc_msg",
    "make_tg_adapter",
    "make_tg_attach_msg",
    "make_tg_msg",
    "mock_channel",
    "telegram_adapter",
]


def make_tg_msg(
    chat_id: int = 42, message_id: int = 10, topic_id: int | None = None
) -> InboundMessage:
    return InboundMessage(
        id=f"telegram:tg:user:1:0:{message_id}",
        platform="telegram",
        bot_id="main",
        scope_id=f"chat:{chat_id}",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(
            chat_id=chat_id,
            message_id=message_id,
            topic_id=topic_id,
            is_group=False,
        ),
        trust_level=TrustLevel.TRUSTED,
    )


def make_dc_msg(channel_id: int = 99, message_id: int = 55) -> InboundMessage:
    return InboundMessage(
        id=f"discord:dc:user:1:0:{message_id}",
        platform="discord",
        bot_id="main",
        scope_id=f"channel:{channel_id}",
        user_id="dc:user:1",
        user_name="Bob",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta=DiscordMeta(
            guild_id=1,
            channel_id=channel_id,
            message_id=message_id,
            thread_id=None,
            channel_type="text",
        ),
        trust_level=TrustLevel.TRUSTED,
    )


def make_tg_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(
        bot_id="main",
        token="tok",
        inbound_bus=MagicMock(),
    )
    bot_mock = AsyncMock()
    bot_mock.send_voice = AsyncMock()
    adapter.bot = bot_mock
    return adapter


def make_dc_adapter() -> DiscordAdapter:
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
    )
    # adapter.http is a discord.py internal uninitialized sentinel in tests.
    # Mock it to raise discord.HTTPException so render_audio falls back to
    # channel.send() as intended, rather than failing with AttributeError.
    adapter.http = MagicMock()
    adapter.http.request = AsyncMock(
        side_effect=discord.HTTPException(
            MagicMock(), "voice not supported in test env"
        )
    )
    return adapter


def make_dc_inbound_msg(
    *,
    channel_id: int = 333,
    message_id: int = 555,
    is_mention: bool = False,
    msg_id: str = "msg-1",
) -> InboundMessage:
    """Build an InboundMessage matching the standard discord test fixture values."""
    return InboundMessage(
        id=msg_id,
        platform="discord",
        bot_id="main",
        scope_id=f"channel:{channel_id}",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=is_mention,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta=DiscordMeta(
            guild_id=111,
            channel_id=channel_id,
            message_id=message_id,
            thread_id=None,
            channel_type="text",
        ),
    )


def attach_typing_cm(mock_channel: MagicMock) -> None:
    """Attach a valid async context manager to mock_channel.typing().

    discord.py's Messageable.typing() returns an async context manager.
    AsyncMock's default auto-spec returns a coroutine instead, which
    causes ``async with messageable.typing()`` to raise TypeError.
    Call this helper on every mock channel used with adapter.send() or
    adapter.send_streaming().
    """
    mock_typing_cm = AsyncMock()
    mock_typing_cm.__aenter__ = AsyncMock(return_value=None)
    mock_typing_cm.__aexit__ = AsyncMock(return_value=False)
    mock_channel.typing = MagicMock(return_value=mock_typing_cm)


def mock_channel() -> MagicMock:
    ch = AsyncMock()
    ch.send = AsyncMock()
    return ch


def make_tg_attach_msg(
    chat_id: int = 42,
    message_id: int = 10,
    topic_id: int | None = None,
    *,
    omit_chat_id: bool = False,
) -> InboundMessage:
    """InboundMessage for Telegram render_attachment tests."""
    return InboundMessage(
        id=f"telegram:tg:user:1:0:{message_id}",
        platform="telegram",
        bot_id="main",
        scope_id=f"chat:{chat_id}",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        trust_level=TrustLevel.TRUSTED,
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(
            chat_id=chat_id if not omit_chat_id else 0,
            message_id=message_id,
            topic_id=topic_id,
            is_group=False,
        ),
    )


def make_dc_attach_msg(
    channel_id: int = 99,
    message_id: int = 55,
    thread_id: int | None = None,
    *,
    omit_channel_id: bool = False,
) -> InboundMessage:
    """InboundMessage for Discord render_attachment tests."""
    return InboundMessage(
        id=f"discord:dc:user:1:0:{message_id}",
        platform="discord",
        bot_id="main",
        scope_id=f"channel:{channel_id}",
        user_id="dc:user:1",
        user_name="Bob",
        is_mention=False,
        text="hi",
        text_raw="hi",
        trust_level=TrustLevel.TRUSTED,
        timestamp=datetime.now(timezone.utc),
        platform_meta=DiscordMeta(
            guild_id=1,
            channel_id=channel_id if not omit_channel_id else 0,
            message_id=message_id,
            thread_id=thread_id,
            channel_type="text",
        ),
    )


@pytest.fixture
def telegram_adapter(mock_inbound_bus):
    adapter = TelegramAdapter(
        bot_id="main",
        token="tok",
        inbound_bus=mock_inbound_bus,
    )
    bot_mock = AsyncMock()
    bot_mock.send_voice = AsyncMock()
    adapter.bot = bot_mock
    return adapter


@pytest.fixture
def discord_adapter(mock_inbound_bus):
    return DiscordAdapter(
        bot_id="main",
        inbound_bus=mock_inbound_bus,
    )
