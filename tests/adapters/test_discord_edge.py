"""Edge-case tests for DiscordAdapter: circuit breaker, backpressure, empty content."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.messaging.message import DiscordMeta
from lyra.core.messaging.messages import MessageManager

from .conftest import attach_typing_cm

TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "config"
    / "messages.toml"
)


# ---------------------------------------------------------------------------
# Circuit breaker helpers
# ---------------------------------------------------------------------------


def _make_open_registry(service: str) -> CircuitRegistry:
    """Build a CircuitRegistry with the named circuit tripped OPEN."""
    registry = CircuitRegistry()
    for name in ("claude-cli", "telegram", "discord", "hub"):
        cb = CircuitBreaker(name, failure_threshold=1, recovery_timeout=60)
        if name == service:
            cb.record_failure()  # trips to OPEN
        registry.register(cb)
    return registry


# ---------------------------------------------------------------------------
# T8 — Missing DISCORD_TOKEN env var → SystemExit
# ---------------------------------------------------------------------------


def test_missing_discord_token_raises_on_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_discord_config() raises SystemExit when DISCORD_TOKEN env var is absent."""
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    from lyra.adapters.discord.discord_config import load_discord_config

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

    inbound_bus = MagicMock()
    inbound_bus.put = AsyncMock(side_effect=asyncio.QueueFull())

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=inbound_bus,
        intents=discord.Intents.none(),
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=None,  # DM — bypasses group-chat filter added in 9f9072d
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        attachments=[],
        reply=AsyncMock(),
    )

    await adapter.on_message(discord_msg)

    discord_msg.reply.assert_awaited_once()  # ack sent


# ---------------------------------------------------------------------------
# SC-11 (Discord) — on_message() drops and notifies user when hub circuit is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_drops_silently_when_hub_circuit_open() -> None:
    """SC-11: on_message() drops (no bus.put) for guild messages that are not mentions.

    Note: this test exercises the mention-filter path — the message never reaches
    push_to_hub_guarded because non-mention guild messages are discarded first.
    For the circuit-open notification path see
    test_on_message_notifies_user_when_hub_circuit_open_dm below.
    """
    from lyra.adapters.discord import DiscordAdapter

    # Arrange
    registry = _make_open_registry("hub")

    inbound_bus = MagicMock()
    inbound_bus.put = AsyncMock()

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=inbound_bus,
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

    # Assert — inbound_bus.put must NOT be called; message filtered before circuit check
    inbound_bus.put.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_notifies_user_when_hub_circuit_open_dm() -> None:
    """SC-11b: DM reaches push_to_hub_guarded; circuit-open drops and notifies user."""
    from lyra.adapters.discord import DiscordAdapter

    # Arrange — hub circuit is OPEN
    registry = _make_open_registry("hub")

    inbound_bus = MagicMock()
    inbound_bus.put = AsyncMock()

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=inbound_bus,
        intents=discord.Intents.none(),
        circuit_registry=registry,
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    reply_mock = AsyncMock()
    # DM: guild=None → always processed, reaches push_to_hub_guarded
    discord_msg = SimpleNamespace(
        guild=None,
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        reply=reply_mock,
        attachments=[],
    )

    # Act
    await adapter.on_message(discord_msg)

    # Assert — inbound_bus.put must NOT be called (message dropped)
    inbound_bus.put.assert_not_called()
    # Assert — user receives a circuit-open notification via reply
    reply_mock.assert_called_once()
    sent_text = reply_mock.call_args.args[0]
    assert "temporarily" in sent_text.lower() or "overloaded" in sent_text.lower()


# ---------------------------------------------------------------------------
# SC-13 (Discord) — send() skips channel.send when discord circuit is OPEN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_skips_when_discord_circuit_open() -> None:
    """SC-13 (updated): adapter.send() no longer checks the CB.
    CB check is owned by OutboundDispatcher. Adapter always delivers.
    """
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.messaging.message import InboundMessage, OutboundMessage

    # Arrange
    registry = _make_open_registry("discord")

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
        circuit_registry=registry,
    )

    mock_message = AsyncMock()
    mock_message.reply = AsyncMock()
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    mock_channel.send = AsyncMock()
    attach_typing_cm(mock_channel)
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
        platform_meta=DiscordMeta(
            guild_id=111,
            channel_id=333,
            message_id=555,
            thread_id=None,
            channel_type="text",
        ),
    )

    # Act
    await adapter.send(hub_msg, OutboundMessage.from_text("hi"))

    # Assert — CB is open but adapter still delivers (CB check in dispatcher)
    mock_message.reply.assert_awaited_once_with("hi")


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

    inbound_bus = MagicMock()
    inbound_bus.put = AsyncMock(side_effect=asyncio.QueueFull())

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=inbound_bus,
        intents=discord.Intents.none(),
        msg_manager=mm,
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    reply_mock = AsyncMock()
    discord_msg = SimpleNamespace(
        guild=None,  # DM — bypasses group-chat filter added in 9f9072d
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        attachments=[],
        reply=reply_mock,
    )

    # Act
    await adapter.on_message(discord_msg)

    # Assert — reply text matches the TOML value for discord backpressure_ack
    expected = mm.get("backpressure_ack", platform="discord")
    reply_mock.assert_awaited_once_with(expected)


# ---------------------------------------------------------------------------
# Empty text edge case
# ---------------------------------------------------------------------------


def test_normalize_empty_text() -> None:
    """normalize() with content="" produces msg.text == ""."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.messaging.message import InboundMessage

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
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
