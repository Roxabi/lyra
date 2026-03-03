"""RED-phase tests for Slice 3: Discord adapter.

All tests in this file are expected to FAIL until the GREEN phase implements:
  - src/lyra/adapters/discord.py  (DiscordAdapter, load_discord_config)

Tests are structured so they are collected by pytest without syntax errors,
but raise ImportError / AttributeError at runtime (not at collection time).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# T2 — _normalize() builds correct DiscordContext
# ---------------------------------------------------------------------------


def test_normalize_builds_correct_discord_context() -> None:
    """_normalize() on a discord message produces correct DiscordContext."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED
    from lyra.core.message import DiscordContext, Platform

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=MagicMock())
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter._normalize(discord_msg)

    assert msg.platform == Platform.DISCORD
    assert msg.platform_context == DiscordContext(
        guild_id=111, channel_id=333, message_id=555
    )


# ---------------------------------------------------------------------------
# T3 — is_mention True when bot is in message.mentions
# ---------------------------------------------------------------------------


def test_is_mention_true_when_bot_in_mentions() -> None:
    """bot_user present in message.mentions → is_mention is True."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=MagicMock())
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="<@999> hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[bot_user],
    )

    msg = adapter._normalize(discord_msg)

    assert msg.is_mention is True


# ---------------------------------------------------------------------------
# T4 — is_mention False when bot is NOT in message.mentions
# ---------------------------------------------------------------------------


def test_is_mention_false_when_bot_not_in_mentions() -> None:
    """bot_user absent from message.mentions → is_mention is False."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=MagicMock())
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter._normalize(discord_msg)

    assert msg.is_mention is False


# ---------------------------------------------------------------------------
# T5 — Own messages (bot author) are filtered — hub.bus.put NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_own_message_is_filtered() -> None:
    """When message.author == adapter._bot_user, hub.bus.put is never called."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    hub.bus = MagicMock()
    hub.bus.put = AsyncMock()

    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=MagicMock())
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=bot_user,  # same object — own message
        content="I just replied",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    await adapter._on_message(discord_msg)

    hub.bus.put.assert_not_awaited()


# ---------------------------------------------------------------------------
# T6 — send() calls discord_msg.reply() when is_mention=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_on_mention() -> None:
    """adapter.send(hub_msg, Response) calls discord_msg.reply(text) when is_mention."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED
    from lyra.core.message import (
        DiscordContext,
        Message,
        MessageType,
        Platform,
        Response,
        TextContent,
    )

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=MagicMock())

    discord_msg = AsyncMock()
    discord_msg.reply = AsyncMock()

    hub_msg = Message(
        id="msg-1",
        platform=Platform.DISCORD,
        bot_id="main",
        channel="discord",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=True,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=DiscordContext(guild_id=111, channel_id=333, message_id=555),
        metadata={"discord_message": discord_msg},
    )
    response = Response(content="hi")

    await adapter.send(hub_msg, response)

    discord_msg.reply.assert_awaited_once_with("hi")


# ---------------------------------------------------------------------------
# T7 — send() calls channel.send() when is_mention=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_channel_on_no_mention() -> None:
    """adapter.send(hub_msg, Response) calls discord_msg.channel.send(text) when not mention."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED
    from lyra.core.message import (
        DiscordContext,
        Message,
        MessageType,
        Platform,
        Response,
        TextContent,
    )

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=MagicMock())

    discord_msg = AsyncMock()
    discord_msg.channel = MagicMock()
    discord_msg.channel.send = AsyncMock()

    hub_msg = Message(
        id="msg-1",
        platform=Platform.DISCORD,
        bot_id="main",
        channel="discord",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=DiscordContext(guild_id=111, channel_id=333, message_id=555),
        metadata={"discord_message": discord_msg},
    )
    response = Response(content="hi")

    await adapter.send(hub_msg, response)

    discord_msg.channel.send.assert_awaited_once_with("hi")


# ---------------------------------------------------------------------------
# T8 — Missing DISCORD_TOKEN env var → SystemExit
# ---------------------------------------------------------------------------


def test_missing_discord_token_raises_on_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_discord_config() raises SystemExit with 'DISCORD_TOKEN' when env var is absent."""
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    from lyra.adapters.discord import load_discord_config  # ImportError expected in RED

    with pytest.raises(SystemExit, match="DISCORD_TOKEN"):
        load_discord_config()
