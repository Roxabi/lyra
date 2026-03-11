"""RED-phase tests for Slice 3: Discord adapter.

All tests in this file are expected to FAIL until the GREEN phase implements:
  - src/lyra/adapters/discord.py  (DiscordAdapter, load_discord_config)

Tests are structured so they are collected by pytest without syntax errors,
but raise ImportError / AttributeError at runtime (not at collection time).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.messages import MessageManager

TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "config"
    / "messages.toml"
)

# ---------------------------------------------------------------------------
# T2 — _normalize() builds correct DiscordContext
# ---------------------------------------------------------------------------


def test_normalize_builds_correct_discord_context() -> None:
    """_normalize() on a discord message produces correct DiscordContext."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED
    from lyra.core.message import DiscordContext, Platform

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
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
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
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
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
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
    """When message.author == adapter._bot_user, inbound_bus.put is never called."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock()

    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
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

    await adapter.on_message(discord_msg)

    hub.inbound_bus.put.assert_not_called()


# ---------------------------------------------------------------------------
# T6 — send() calls msg.reply() when is_mention=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_on_mention() -> None:
    """adapter.send() calls msg.reply(text) when is_mention=True."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import (
        DiscordContext,
        Message,
        MessageType,
        Platform,
        Response,
        TextContent,
    )

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())

    mock_message = AsyncMock()
    mock_message.reply = AsyncMock()
    mock_channel = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=mock_message)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = Message(
        id="msg-1",
        platform=Platform.DISCORD,
        bot_id="main",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=True,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=DiscordContext(guild_id=111, channel_id=333, message_id=555),
    )
    response = Response(content="hi")

    await adapter.send(hub_msg, response)

    mock_channel.fetch_message.assert_awaited_once_with(555)
    mock_message.reply.assert_awaited_once_with("hi")


# ---------------------------------------------------------------------------
# T7 — send() calls channel.send() when is_mention=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_channel_on_no_mention() -> None:
    """send() calls channel.send(text) when is_mention=False."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import (
        DiscordContext,
        Message,
        MessageType,
        Platform,
        Response,
        TextContent,
    )

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())

    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock()
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = Message(
        id="msg-1",
        platform=Platform.DISCORD,
        bot_id="main",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=DiscordContext(guild_id=111, channel_id=333, message_id=555),
    )
    response = Response(content="hi")

    await adapter.send(hub_msg, response)

    mock_channel.send.assert_awaited_once_with("hi")


# ---------------------------------------------------------------------------
# T8 — Missing DISCORD_TOKEN env var → SystemExit
# ---------------------------------------------------------------------------


def test_missing_discord_token_raises_on_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_discord_config() raises SystemExit when DISCORD_TOKEN env var is absent."""
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    from lyra.adapters.discord import load_discord_config  # ImportError expected in RED

    with pytest.raises(SystemExit, match="DISCORD_TOKEN"):
        load_discord_config()


# ---------------------------------------------------------------------------
# T9 — Backpressure: bus full → send ack before putting to bus (S5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backpressure_sends_ack_when_bus_full() -> None:
    """When inbound queue is full, put raises QueueFull and adapter sends ack."""
    import asyncio

    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock(side_effect=asyncio.QueueFull())

    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        reply=AsyncMock(),
    )

    await adapter.on_message(discord_msg)

    discord_msg.reply.assert_awaited_once()  # ack sent


# ---------------------------------------------------------------------------
# T10 — Cold-start: _bot_user=None → is_mention False, no crash
# ---------------------------------------------------------------------------


def test_normalize_bot_user_none_is_mention_false() -> None:
    """When _bot_user is None (pre-on_ready), is_mention must be False — never raise."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    # _bot_user stays None (default, before on_ready fires)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[SimpleNamespace(id=999)],  # would match if _bot_user were set
    )

    msg = adapter._normalize(discord_msg)

    assert msg.is_mention is False  # no crash, returns False


# ---------------------------------------------------------------------------
# T11 — Mention stripping: @mention prefix stripped from content
# ---------------------------------------------------------------------------


def test_mention_prefix_stripped_from_content() -> None:
    """@mention prefix (<@id>) is stripped from content before delivery."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="<@999> hello world",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[bot_user],
    )

    from lyra.core.message import TextContent

    msg = adapter._normalize(discord_msg)

    assert isinstance(msg.content, TextContent)
    assert msg.content.text == "hello world"


def test_mention_prefix_stripped_nickname_variant() -> None:
    """@mention prefix with nickname format (<@!id>) is also stripped."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="<@!999> hello world",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[bot_user],
    )

    from lyra.core.message import TextContent

    msg = adapter._normalize(discord_msg)

    assert isinstance(msg.content, TextContent)
    assert msg.content.text == "hello world"


# ---------------------------------------------------------------------------
# T12 — DM (guild=None) normalization: guild_id=0, no AttributeError
# ---------------------------------------------------------------------------


def test_normalize_dm_no_guild() -> None:
    """DM messages (guild=None) normalize with guild_id=None — no AttributeError."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import DiscordContext

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=None,  # DM — no guild
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter._normalize(discord_msg)

    assert msg.platform_context == DiscordContext(
        guild_id=None, channel_id=333, message_id=555
    )


# ---------------------------------------------------------------------------
# T13 — display_name: takes precedence over name when present
# ---------------------------------------------------------------------------


def test_normalize_uses_display_name_when_present() -> None:
    """When author has display_name, it takes precedence over name."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(
            id=42, name="alice_raw", display_name="Alice Display", bot=False
        ),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter._normalize(discord_msg)

    assert msg.user_name == "Alice Display"


def test_normalize_falls_back_to_name_when_display_name_none() -> None:
    """When display_name is None, falls back to name."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="alice_raw", display_name=None, bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter._normalize(discord_msg)

    assert msg.user_name == "alice_raw"


# ---------------------------------------------------------------------------
# A9 — Token-in-logs security test
# ---------------------------------------------------------------------------


def test_discord_token_not_in_logs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Discord bot token must never appear in log output at any log level."""
    import logging

    from lyra.adapters.discord import DiscordAdapter

    secret_token = "super-secret-discord-token-xyz"
    monkeypatch.setenv("DISCORD_TOKEN", secret_token)

    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())

    with caplog.at_level(logging.DEBUG):
        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="hello",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
        )
        adapter._normalize(discord_msg)

    for record in caplog.records:
        assert secret_token not in record.getMessage(), (
            f"Token found in log at {record.levelname}: {record.getMessage()!r}"
        )


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
# SC-11 (Discord) — on_message() drops silently when hub circuit is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_drops_silently_when_hub_circuit_open() -> None:
    """SC-11: on_message() drops silently (no bus.put) when circuits['hub'] is OPEN."""
    from lyra.adapters.discord import DiscordAdapter

    # Arrange
    registry = _make_open_registry("hub")

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock()

    adapter = DiscordAdapter(
        hub=hub,
        bot_id="main",
        intents=discord.Intents.none(),
        circuit_registry=registry,
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        reply=AsyncMock(),
    )

    # Act
    await adapter.on_message(discord_msg)

    # Assert — inbound_bus.put must NOT be called; message was silently dropped
    hub.inbound_bus.put.assert_not_called()


# ---------------------------------------------------------------------------
# SC-13 (Discord) — send() skips channel.send when discord circuit is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_skips_when_discord_circuit_open() -> None:
    """SC-13 (updated): adapter.send() no longer checks the CB.
    CB check is owned by OutboundDispatcher. Adapter always delivers.
    """
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import (
        DiscordContext,
        Message,
        MessageType,
        Platform,
        Response,
        TextContent,
    )

    # Arrange
    registry = _make_open_registry("discord")

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub,
        bot_id="main",
        intents=discord.Intents.none(),
        circuit_registry=registry,
    )

    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock()
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = Message(
        id="msg-1",
        platform=Platform.DISCORD,
        bot_id="main",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=DiscordContext(guild_id=111, channel_id=333, message_id=555),
    )
    response = Response(content="hi")

    # Act
    await adapter.send(hub_msg, response)

    # Assert — CB is open but adapter still calls channel.send (CB check in dispatcher)
    mock_channel.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# msg_manager injection — backpressure_ack uses TOML string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discord_msg_manager_injection_backpressure_ack() -> None:
    """Injecting a real MessageManager causes on_message to reply with the TOML
    'backpressure_ack' string (not the hardcoded fallback) when bus is full."""
    from lyra.adapters.discord import DiscordAdapter

    # Arrange
    mm = MessageManager(TOML_PATH)

    import asyncio

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock(side_effect=asyncio.QueueFull())

    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), msg_manager=mm
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    reply_mock = AsyncMock()
    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        reply=reply_mock,
    )

    # Act
    await adapter.on_message(discord_msg)

    # Assert — reply text matches the TOML value for discord backpressure_ack
    expected = mm.get("backpressure_ack", platform="discord")
    reply_mock.assert_awaited_once_with(expected)


# ---------------------------------------------------------------------------
# T14 — send() stores bot's reply message_id in response.metadata (channel.send)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_stores_reply_message_id_channel_send() -> None:
    """send() via channel.send() stores sent message id in response.metadata."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import (
        DiscordContext,
        Message,
        MessageType,
        Platform,
        Response,
        TextContent,
    )

    # Arrange
    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())

    sent_msg = SimpleNamespace(id=888)
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock(return_value=sent_msg)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = Message(
        id="msg-1",
        platform=Platform.DISCORD,
        bot_id="main",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=DiscordContext(guild_id=111, channel_id=333, message_id=555),
    )
    response = Response(content="hi")

    # Act
    await adapter.send(hub_msg, response)

    # Assert
    mock_channel.send.assert_awaited_once_with("hi")
    assert response.metadata["reply_message_id"] == 888


# ---------------------------------------------------------------------------
# T15 — send() stores bot's reply message_id in response.metadata (msg.reply)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_stores_reply_message_id_msg_reply() -> None:
    """send() via msg.reply() stores sent message id in response.metadata."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import (
        DiscordContext,
        Message,
        MessageType,
        Platform,
        Response,
        TextContent,
    )

    # Arrange
    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())

    sent_msg = SimpleNamespace(id=7777)
    mock_message = AsyncMock()
    mock_message.reply = AsyncMock(return_value=sent_msg)
    mock_channel = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=mock_message)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = Message(
        id="msg-1",
        platform=Platform.DISCORD,
        bot_id="main",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=True,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=DiscordContext(guild_id=111, channel_id=333, message_id=555),
    )
    response = Response(content="hi")

    # Act
    await adapter.send(hub_msg, response)

    # Assert
    mock_channel.fetch_message.assert_awaited_once_with(555)
    mock_message.reply.assert_awaited_once_with("hi")
    assert response.metadata["reply_message_id"] == 7777


# ---------------------------------------------------------------------------
# T16 — send() does NOT set reply_message_id when send fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_no_reply_message_id_on_failure() -> None:
    """send() must NOT set reply_message_id in metadata when the send call throws."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import (
        DiscordContext,
        Message,
        MessageType,
        Platform,
        Response,
        TextContent,
    )

    # Arrange
    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())

    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock(side_effect=Exception("network error"))
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = Message(
        id="msg-1",
        platform=Platform.DISCORD,
        bot_id="main",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=TextContent(text="hello"),
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=DiscordContext(guild_id=111, channel_id=333, message_id=555),
    )
    response = Response(content="hi")

    # Act — send() now raises on failure (CB recording handled by OutboundDispatcher)
    with pytest.raises(Exception, match="network error"):
        await adapter.send(hub_msg, response)

    # Assert
    assert "reply_message_id" not in response.metadata


# ---------------------------------------------------------------------------
# Tests for Discord auto_thread (issue #127)
# ---------------------------------------------------------------------------
# RED-phase tests — describe behaviour implemented by backend-dev in T5/T7.
# These run after the implementation is complete.


class TestDiscordAutoThread:
    """DiscordAdapter creates a thread on @mention in text channels (S5-1..S5-5)."""

    @pytest.mark.asyncio
    async def test_auto_thread_created_on_mention_in_text_channel(self) -> None:
        """@mention in a text channel with auto_thread=True → create_thread() called."""
        from unittest.mock import patch

        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.message import (
            DiscordContext,
            Message,
            MessageType,
            Platform,
            TextContent,
        )

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        thread_mock = MagicMock()
        thread_mock.id = 9999
        create_thread_mock = AsyncMock(return_value=thread_mock)

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(
                id=333,
                send=AsyncMock(),
                type=SimpleNamespace(name="text"),
            ),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        # hub_msg with channel_type="text" and is_mention=True for the normalize patch
        hub_msg = Message.from_adapter(
            platform=Platform.DISCORD,
            bot_id="main",
            user_id="dc:user:42",
            user_name="Alice",
            content=TextContent(text="help me"),
            type=MessageType.TEXT,
            timestamp=datetime.now(timezone.utc),
            is_mention=True,
            platform_context=DiscordContext(
                guild_id=111,
                channel_id=333,
                message_id=555,
                thread_id=None,
                channel_type="text",
            ),
        )

        with patch.object(adapter, "_normalize", return_value=hub_msg):
            await adapter.on_message(discord_msg)

        # Assert — create_thread was called once
        create_thread_mock.assert_awaited_once()

        # Assert — hub_msg.platform_context now has thread_id = 9999
        assert isinstance(hub_msg.platform_context, DiscordContext)
        assert hub_msg.platform_context.thread_id == 9999

    @pytest.mark.asyncio
    async def test_auto_thread_not_created_in_existing_thread(self) -> None:
        """@mention in an existing thread channel does NOT call create_thread()."""
        from unittest.mock import patch

        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.message import (
            DiscordContext,
            Message,
            MessageType,
            Platform,
            TextContent,
        )

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        create_thread_mock = AsyncMock()

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        # channel_type="thread" — already in a thread, must NOT create another
        hub_msg = Message.from_adapter(
            platform=Platform.DISCORD,
            bot_id="main",
            user_id="dc:user:42",
            user_name="Alice",
            content=TextContent(text="help me"),
            type=MessageType.TEXT,
            timestamp=datetime.now(timezone.utc),
            is_mention=True,
            platform_context=DiscordContext(
                guild_id=111,
                channel_id=333,
                message_id=555,
                thread_id=777,
                channel_type="thread",
            ),
        )

        with patch.object(adapter, "_normalize", return_value=hub_msg):
            await adapter.on_message(discord_msg)

        # Assert — create_thread NOT called when already in a thread
        create_thread_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_thread_disabled(self) -> None:
        """auto_thread=False → create_thread() is never called even on @mention."""
        from unittest.mock import patch

        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.message import (
            DiscordContext,
            Message,
            MessageType,
            Platform,
            TextContent,
        )

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=False,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        create_thread_mock = AsyncMock()

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        hub_msg = Message.from_adapter(
            platform=Platform.DISCORD,
            bot_id="main",
            user_id="dc:user:42",
            user_name="Alice",
            content=TextContent(text="help me"),
            type=MessageType.TEXT,
            timestamp=datetime.now(timezone.utc),
            is_mention=True,
            platform_context=DiscordContext(
                guild_id=111,
                channel_id=333,
                message_id=555,
                thread_id=None,
                channel_type="text",
            ),
        )

        with patch.object(adapter, "_normalize", return_value=hub_msg):
            await adapter.on_message(discord_msg)

        # Assert — auto_thread=False → no thread created
        create_thread_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_thread_exception_fallback(self) -> None:
        """create_thread() raising Exception: message still processed in original ch."""
        from unittest.mock import patch

        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.message import (
            DiscordContext,
            Message,
            MessageType,
            Platform,
            TextContent,
        )

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        # create_thread raises — adapter must fall through and put msg on bus
        create_thread_mock = AsyncMock(side_effect=Exception("discord unavailable"))

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        hub_msg = Message.from_adapter(
            platform=Platform.DISCORD,
            bot_id="main",
            user_id="dc:user:42",
            user_name="Alice",
            content=TextContent(text="help me"),
            type=MessageType.TEXT,
            timestamp=datetime.now(timezone.utc),
            is_mention=True,
            platform_context=DiscordContext(
                guild_id=111,
                channel_id=333,
                message_id=555,
                thread_id=None,
                channel_type="text",
            ),
        )

        with patch.object(adapter, "_normalize", return_value=hub_msg):
            # Act — must not raise
            await adapter.on_message(discord_msg)

        # Assert — message still processed (bus.put called)
        hub.inbound_bus.put.assert_called_once()

    def test_discord_config_auto_thread_default_true(self) -> None:
        """DiscordConfig() has auto_thread=True by default (S5-5)."""
        from lyra.adapters.discord import DiscordConfig

        # Arrange / Act
        config = DiscordConfig(token="dummy-token")  # type: ignore[call-arg]

        # Assert
        assert config.auto_thread is True
