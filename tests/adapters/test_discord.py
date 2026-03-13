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

from lyra.adapters.discord import _ALLOW_ALL
from lyra.core.auth import TrustLevel
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
    """normalize() on a discord message produces correct platform_meta."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED
    from lyra.core.message import InboundMessage

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
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

    msg = adapter.normalize(discord_msg)

    assert isinstance(msg, InboundMessage)
    assert msg.platform == "discord"
    assert msg.scope_id == "channel:333"
    assert msg.platform_meta["guild_id"] == 111
    assert msg.platform_meta["channel_id"] == 333
    assert msg.platform_meta["message_id"] == 555
    assert msg.platform_meta["channel_type"] == "text"


# ---------------------------------------------------------------------------
# T3 — is_mention True when bot is in message.mentions
# ---------------------------------------------------------------------------


def test_is_mention_true_when_bot_in_mentions() -> None:
    """bot_user present in message.mentions → is_mention is True."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
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

    msg = adapter.normalize(discord_msg)

    assert msg.is_mention is True


# ---------------------------------------------------------------------------
# T4 — is_mention False when bot is NOT in message.mentions
# ---------------------------------------------------------------------------


def test_is_mention_false_when_bot_not_in_mentions() -> None:
    """bot_user absent from message.mentions → is_mention is False."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
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
    )

    msg = adapter.normalize(discord_msg)

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

    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
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
    from lyra.core.message import InboundMessage, OutboundMessage

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    mock_message = AsyncMock()
    mock_message.reply = AsyncMock()
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = InboundMessage(
        id="msg-1",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=True,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "guild_id": 111,
            "channel_id": 333,
            "message_id": 555,
            "thread_id": None,
            "channel_type": "text",
        },
        trust_level=TrustLevel.TRUSTED,
    )

    await adapter.send(hub_msg, OutboundMessage.from_text("hi"))

    mock_channel.get_partial_message.assert_called_once_with(555)
    mock_message.reply.assert_awaited_once_with("hi")


# ---------------------------------------------------------------------------
# T7 — send() calls channel.send() when is_mention=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_channel_on_no_mention() -> None:
    """send() calls channel.send(text) when is_mention=False."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import InboundMessage, OutboundMessage

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock()
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = InboundMessage(
        id="msg-1",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "guild_id": 111,
            "channel_id": 333,
            "message_id": 555,
            "thread_id": None,
            "channel_type": "text",
        },
        trust_level=TrustLevel.TRUSTED,
    )

    await adapter.send(hub_msg, OutboundMessage.from_text("hi"))

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

    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
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

    await adapter.on_message(discord_msg)

    discord_msg.reply.assert_awaited_once()  # ack sent


# ---------------------------------------------------------------------------
# T10 — Cold-start: _bot_user=None → is_mention False, no crash
# ---------------------------------------------------------------------------


def test_normalize_bot_user_none_is_mention_false() -> None:
    """When _bot_user is None (pre-on_ready), is_mention must be False — never raise."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
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

    msg = adapter.normalize(discord_msg)

    assert msg.is_mention is False  # no crash, returns False


# ---------------------------------------------------------------------------
# T11 — Mention stripping: @mention prefix stripped from content
# ---------------------------------------------------------------------------


def test_mention_prefix_stripped_from_content() -> None:
    """@mention prefix (<@id>) is stripped from text before delivery."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
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

    msg = adapter.normalize(discord_msg)

    assert msg.text == "hello world"
    assert msg.text_raw == "<@999> hello world"


def test_mention_prefix_stripped_nickname_variant() -> None:
    """@mention prefix with nickname format (<@!id>) is also stripped."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
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

    msg = adapter.normalize(discord_msg)

    assert msg.text == "hello world"


# ---------------------------------------------------------------------------
# T12 — DM (guild=None) normalization: guild_id=0, no AttributeError
# ---------------------------------------------------------------------------


def test_normalize_dm_no_guild() -> None:
    """DM messages (guild=None) normalize with guild_id=None — no AttributeError."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import InboundMessage

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=None,  # DM — no guild
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter.normalize(discord_msg)

    assert isinstance(msg, InboundMessage)
    assert msg.platform_meta["guild_id"] is None
    assert msg.platform_meta["channel_id"] == 333
    assert msg.platform_meta["message_id"] == 555


# ---------------------------------------------------------------------------
# T13 — display_name: takes precedence over name when present
# ---------------------------------------------------------------------------


def test_normalize_uses_display_name_when_present() -> None:
    """When author has display_name, it takes precedence over name."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
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

    msg = adapter.normalize(discord_msg)

    assert msg.user_name == "Alice Display"


def test_normalize_falls_back_to_name_when_display_name_none() -> None:
    """When display_name is None, falls back to name."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
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

    msg = adapter.normalize(discord_msg)

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
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

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
        adapter.normalize(discord_msg)

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
        auth=_ALLOW_ALL,
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
    from lyra.core.message import InboundMessage, OutboundMessage

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

    hub_msg = InboundMessage(
        id="msg-1",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta={
            "guild_id": 111,
            "channel_id": 333,
            "message_id": 555,
            "thread_id": None,
            "channel_type": "text",
        },
    )

    # Act
    await adapter.send(hub_msg, OutboundMessage.from_text("hi"))

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
        hub=hub,
        bot_id="main",
        intents=discord.Intents.none(),
        msg_manager=mm,
        auth=_ALLOW_ALL,
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
    """send() via channel.send() stores sent message id in outbound.metadata."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import InboundMessage, OutboundMessage

    # Arrange
    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    sent_msg = SimpleNamespace(id=888)
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock(return_value=sent_msg)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = InboundMessage(
        id="msg-1",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta={
            "guild_id": 111,
            "channel_id": 333,
            "message_id": 555,
            "thread_id": None,
            "channel_type": "text",
        },
    )
    outbound = OutboundMessage.from_text("hi")

    # Act
    await adapter.send(hub_msg, outbound)

    # Assert
    mock_channel.send.assert_awaited_once_with("hi")
    assert outbound.metadata["reply_message_id"] == 888


# ---------------------------------------------------------------------------
# T15 — send() stores bot's reply message_id in response.metadata (msg.reply)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_stores_reply_message_id_msg_reply() -> None:
    """send() via msg.reply() stores sent message id in outbound.metadata."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import InboundMessage, OutboundMessage

    # Arrange
    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    sent_msg = SimpleNamespace(id=7777)
    mock_message = AsyncMock()
    mock_message.reply = AsyncMock(return_value=sent_msg)
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = InboundMessage(
        id="msg-1",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=True,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta={
            "guild_id": 111,
            "channel_id": 333,
            "message_id": 555,
            "thread_id": None,
            "channel_type": "text",
        },
    )
    outbound = OutboundMessage.from_text("hi")

    # Act
    await adapter.send(hub_msg, outbound)

    # Assert
    mock_channel.get_partial_message.assert_called_once_with(555)
    mock_message.reply.assert_awaited_once_with("hi")
    assert outbound.metadata["reply_message_id"] == 7777


# ---------------------------------------------------------------------------
# T16 — send() does NOT set reply_message_id when send fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_no_reply_message_id_on_failure() -> None:
    """send() must NOT set reply_message_id in metadata when the send call throws."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import InboundMessage, OutboundMessage

    # Arrange
    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock(side_effect=Exception("network error"))
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = InboundMessage(
        id="msg-1",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta={
            "guild_id": 111,
            "channel_id": 333,
            "message_id": 555,
            "thread_id": None,
            "channel_type": "text",
        },
    )
    outbound = OutboundMessage.from_text("hi")

    # Act — send() now raises on failure (CB recording handled by OutboundDispatcher)
    with pytest.raises(Exception, match="network error"):
        await adapter.send(hub_msg, outbound)

    # Assert
    assert "reply_message_id" not in outbound.metadata


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
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
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
                create_thread=AsyncMock(),  # needed for hasattr check in on_message
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

        await adapter.on_message(discord_msg)

        # Assert — create_thread was called once
        create_thread_mock.assert_awaited_once()

        # Assert — hub.inbound_bus.put was called and the InboundMessage has thread_id
        hub.inbound_bus.put.assert_called_once()
        _platform_arg, hub_msg = hub.inbound_bus.put.call_args[0]
        assert hub_msg.platform_meta["thread_id"] == 9999
        assert hub_msg.scope_id == "thread:9999"

    @pytest.mark.asyncio
    async def test_auto_thread_not_created_in_existing_thread(self) -> None:
        """@mention in an existing thread channel does NOT call create_thread()."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        create_thread_mock = AsyncMock()

        # Message already in an existing discord.Thread (isinstance check)
        existing_thread = MagicMock(spec=discord.Thread)
        existing_thread.id = 777

        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=existing_thread,
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="<@999> help me",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[bot_user],
            create_thread=create_thread_mock,
        )

        await adapter.on_message(discord_msg)

        # Assert — create_thread NOT called when already in a thread
        create_thread_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_thread_disabled(self) -> None:
        """auto_thread=False → create_thread() is never called even on @mention."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=False,
            auth=_ALLOW_ALL,
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

        await adapter.on_message(discord_msg)

        # Assert — auto_thread=False → no thread created
        create_thread_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_thread_exception_fallback(self) -> None:
        """create_thread() raising Exception: message still processed in original ch."""
        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()

        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            intents=discord.Intents.none(),
            auto_thread=True,
            auth=_ALLOW_ALL,
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


def test_normalize_empty_text() -> None:
    """normalize() with content=\"\" produces msg.text == \"\"."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import InboundMessage

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)
    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )
    msg = adapter.normalize(discord_msg)
    assert isinstance(msg, InboundMessage)
    assert msg.text == ""


# ---------------------------------------------------------------------------
# RED — Slice 4: OutboundMessage render tests for DiscordAdapter (#138)
# ---------------------------------------------------------------------------

from lyra.core.message import (  # noqa: E402,F401 — Slice V2 green
    Attachment,
    Button,
    CodeBlock,
    OutboundMessage,
)


def _make_discord_adapter():
    """Build a DiscordAdapter with a MagicMock hub."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    return DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )


def _make_discord_message(*, is_mention: bool = False):
    """Build a minimal InboundMessage for adapter.send() calls."""
    from datetime import datetime, timezone

    from lyra.core.message import InboundMessage

    return InboundMessage(
        id="msg-dc-138",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=is_mention,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta={
            "guild_id": 111,
            "channel_id": 333,
            "message_id": 555,
            "thread_id": None,
            "channel_type": "text",
        },
    )


class TestDiscordOutboundMessage:
    """Slice 4 RED tests — DiscordAdapter rendering of OutboundMessage."""

    @pytest.mark.asyncio
    async def test_send_accepts_outbound_message(self) -> None:
        """adapter.send(msg, OutboundMessage.from_text("hello")) calls channel.send."""
        # Arrange
        adapter = _make_discord_adapter()

        sent_mock = SimpleNamespace(id=88)
        mock_channel = AsyncMock()
        mock_channel.send = AsyncMock(return_value=sent_mock)
        adapter.get_channel = MagicMock(return_value=mock_channel)

        outbound = OutboundMessage.from_text("hello")
        original_msg = _make_discord_message()

        # Act
        await adapter.send(original_msg, outbound)

        # Assert
        mock_channel.send.assert_awaited()

    def test_render_text_empty_returns_no_chunks(self) -> None:
        """_render_text("") returns [] — no empty-string chunk to send to the API."""
        # Arrange
        adapter = _make_discord_adapter()

        # Act
        chunks = adapter._render_text("")  # type: ignore[attr-defined]

        # Assert
        assert chunks == []

    def test_render_text_chunks_at_2000(self) -> None:
        """_render_text("x" * 2500) returns 2 chunks, each ≤ 2000 characters."""
        # Arrange
        adapter = _make_discord_adapter()
        text = "x" * 2500

        # Act
        chunks = adapter._render_text(text)  # type: ignore[attr-defined]

        # Assert
        assert len(chunks) == 2
        assert all(len(c) <= 2000 for c in chunks)

    def test_render_buttons_none_when_empty(self) -> None:
        """_render_buttons([]) returns None."""
        # Arrange
        adapter = _make_discord_adapter()

        # Act
        result = adapter._render_buttons([])  # type: ignore[attr-defined]

        # Assert
        assert result is None

    def test_render_buttons_returns_view(self) -> None:
        """_render_buttons([Button("Yes","yes")]) returns a discord.ui.View."""
        # Arrange
        adapter = _make_discord_adapter()

        # Act
        result = adapter._render_buttons([Button("Yes", "yes")])  # type: ignore[attr-defined]

        # Assert
        assert isinstance(result, discord.ui.View)

    @pytest.mark.asyncio
    async def test_buttons_only_on_last_chunk(self) -> None:
        """Sending OutboundMessage with long content + buttons: first channel.send
        call has no view (or view=None), second (last) call has view set."""
        # Arrange
        adapter = _make_discord_adapter()

        calls: list[dict] = []

        async def capture_send(*args, **kwargs):  # type: ignore[return]
            calls.append(dict(kwargs))
            return SimpleNamespace(id=len(calls))

        mock_channel = AsyncMock()
        mock_channel.send = capture_send
        adapter.get_channel = MagicMock(return_value=mock_channel)

        outbound = OutboundMessage(
            content=["x" * 2500],
            buttons=[Button("Yes", "yes")],
        )
        original_msg = _make_discord_message(is_mention=False)

        # Act
        await adapter.send(original_msg, outbound)

        # Assert — two send calls made (2500 chars → 2 chunks of ≤ 2000)
        assert len(calls) == 2, f"Expected 2 channel.send calls, got {len(calls)}"
        # First chunk: no view, or view is None
        assert calls[0].get("view") is None or "view" not in calls[0]
        # Last chunk: view is set (truthy)
        assert calls[1].get("view") is not None

    @pytest.mark.asyncio
    async def test_reply_message_id_stored_in_metadata(self) -> None:
        """send() stores the reply message id in outbound.metadata."""
        # Arrange
        adapter = _make_discord_adapter()

        sent_mock = SimpleNamespace(id=7654)
        mock_channel = AsyncMock()
        mock_channel.send = AsyncMock(return_value=sent_mock)
        adapter.get_channel = MagicMock(return_value=mock_channel)

        outbound = OutboundMessage.from_text("hi")
        original_msg = _make_discord_message()

        # Act
        await adapter.send(original_msg, outbound)

        # Assert
        assert outbound.metadata.get("reply_message_id") == 7654


# ---------------------------------------------------------------------------
# Inbound attachment extraction (#183)
# ---------------------------------------------------------------------------


class TestDiscordAttachments:
    """DiscordAdapter.normalize() extracts non-audio attachments."""

    def _make_adapter(self):
        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
        adapter._bot_user = SimpleNamespace(id=999, bot=True)
        return adapter

    def _make_msg(self, *, attachments=None, content="hello"):
        return SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice",
                display_name="Alice", bot=False,
            ),
            content=content,
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
            attachments=attachments,
        )

    def test_normalize_image_attachment(self) -> None:
        """Image attachment → type='image', CDN URL, mime_type."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="image/png",
            url="https://cdn.discord.com/img.png",
            filename="img.png",
        )
        msg = adapter.normalize(
            self._make_msg(attachments=[att]),
        )
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "image"
        assert a.url_or_path_or_bytes == "https://cdn.discord.com/img.png"
        assert a.mime_type == "image/png"
        assert a.filename == "img.png"

    def test_normalize_document_attachment(self) -> None:
        """Non-image/video/audio → type='file', correct filename."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="application/pdf",
            url="https://cdn.discord.com/doc.pdf",
            filename="doc.pdf",
        )
        msg = adapter.normalize(
            self._make_msg(attachments=[att]),
        )
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "file"
        assert a.filename == "doc.pdf"
        assert a.mime_type == "application/pdf"

    def test_normalize_multiple_attachments(self) -> None:
        """Multiple non-audio attachments → all in list."""
        adapter = self._make_adapter()
        atts = [
            SimpleNamespace(
                content_type="image/jpeg",
                url="https://cdn/a.jpg",
                filename="a.jpg",
            ),
            SimpleNamespace(
                content_type="application/pdf",
                url="https://cdn/b.pdf",
                filename="b.pdf",
            ),
        ]
        msg = adapter.normalize(
            self._make_msg(attachments=atts),
        )
        assert len(msg.attachments) == 2

    def test_normalize_video_attachment(self) -> None:
        """Video content_type → type='video'."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="video/mp4",
            url="https://cdn.discord.com/clip.mp4",
            filename="clip.mp4",
        )
        msg = adapter.normalize(
            self._make_msg(attachments=[att]),
        )
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "video"
        assert a.mime_type == "video/mp4"

    def test_normalize_audio_attachment_excluded(self) -> None:
        """Audio content_type → NOT in attachments list."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="audio/ogg",
            url="https://cdn.discord.com/voice.ogg",
            filename="voice.ogg",
        )
        msg = adapter.normalize(
            self._make_msg(attachments=[att]),
        )
        assert len(msg.attachments) == 0

    def test_normalize_text_only_empty_attachments(self) -> None:
        """Message without attachments → empty list."""
        adapter = self._make_adapter()
        msg = adapter.normalize(self._make_msg())
        assert msg.attachments == []

    def test_normalize_text_and_image(self) -> None:
        """Text AND image → both populated."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="image/png",
            url="https://cdn/img.png",
            filename="img.png",
        )
        msg = adapter.normalize(
            self._make_msg(
                content="check this out",
                attachments=[att],
            ),
        )
        assert msg.text == "check this out"
        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "image"


# ---------------------------------------------------------------------------
# Slice S5: DiscordAdapter auth gate tests
# ---------------------------------------------------------------------------


def _make_discord_msg_ns(user_id: int = 42, roles: list | None = None) -> object:
    """Build a minimal discord-like message SimpleNamespace."""
    author_kwargs: dict = {
        "id": user_id,
        "name": "Alice",
        "display_name": "Alice",
        "bot": False,
    }
    if roles is not None:
        author_kwargs["roles"] = [SimpleNamespace(id=r) for r in roles]
    return SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(**author_kwargs),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        reply=AsyncMock(),
        attachments=[],
    )


class TestDiscordAuth:
    """Auth gate tests for DiscordAdapter.on_message."""

    @pytest.mark.asyncio
    async def test_blocked_user_skips_normalize(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """BLOCKED user: on_message returns early without calling normalize()."""
        import logging
        from unittest.mock import patch

        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.auth import AuthMiddleware, TrustLevel

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.BLOCKED

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        with caplog.at_level(logging.INFO, logger="lyra.adapters.discord"):
            with patch.object(adapter, "normalize") as mock_norm:
                await adapter.on_message(_make_discord_msg_ns())

        mock_norm.assert_not_called()
        hub.inbound_bus.put.assert_not_called()
        assert any("auth_reject" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_role_match_returns_trust(self) -> None:
        """User with a matching role: message produced with correct trust_level."""
        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.auth import AuthMiddleware, TrustLevel

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.TRUSTED

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        msg_ns = _make_discord_msg_ns(roles=["123456"])
        await adapter.on_message(msg_ns)

        # Verify role snowflake IDs were passed to auth.check
        call_kwargs = auth.check.call_args
        assert call_kwargs is not None
        passed_roles = call_kwargs.kwargs.get("roles") or (
            call_kwargs.args[1] if len(call_kwargs.args) > 1 else []
        )
        assert "123456" in passed_roles

    @pytest.mark.asyncio
    async def test_dm_fallback_user_id_only(self) -> None:
        """DM message (no roles attribute): auth.check called with roles=[]."""
        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.auth import AuthMiddleware, TrustLevel

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.PUBLIC

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        # No roles attribute on author (DM scenario)
        dm_msg = SimpleNamespace(
            guild=None,
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="hello",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
            reply=AsyncMock(),
            attachments=[],
        )

        await adapter.on_message(dm_msg)

        call_kwargs = auth.check.call_args
        assert call_kwargs is not None
        passed_roles = call_kwargs.kwargs.get("roles") or (
            call_kwargs.args[1] if len(call_kwargs.args) > 1 else []
        )
        assert passed_roles == []

    @pytest.mark.asyncio
    async def test_public_user_message_forwarded(self) -> None:
        """PUBLIC user: message reaches bus with trust_level=TrustLevel.PUBLIC."""
        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.auth import AuthMiddleware, TrustLevel

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.PUBLIC

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        msg_ns = _make_discord_msg_ns()
        await adapter.on_message(msg_ns)

        hub.inbound_bus.put.assert_called_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.trust_level == TrustLevel.PUBLIC
