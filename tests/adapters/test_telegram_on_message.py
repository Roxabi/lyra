"""Tests for TelegramAdapter._on_message(): backpressure, bot-drop, circuit breaker.

Covers: T6, SC-11.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.telegram import _ALLOW_ALL
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
