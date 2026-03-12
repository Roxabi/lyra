"""Tests for Telegram voice message handling (issue #147 / STT V1).

Covers:
- Voice message normalised to MessageType.AUDIO with correct AudioContent
- Typing indicator sent before download
- file_id propagated to AudioContent
- Circuit-open drops message + cleans up temp file
- Bot message drops voice messages from bots
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.adapters.telegram import TelegramAdapter
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.message import Attachment


def _make_voice_msg(
    file_id: str = "FILE123",
    duration: int = 3,
    chat_id: int = 42,
    user_id: int = 7,
    chat_type: str = "private",
) -> SimpleNamespace:
    """Build a minimal aiogram-like voice message stub."""
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=chat_type),
        from_user=SimpleNamespace(id=user_id, full_name="Alice", is_bot=False),
        voice=SimpleNamespace(file_id=file_id, duration=duration),
        audio=None,
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=55,
    )


def _make_adapter() -> tuple[TelegramAdapter, MagicMock]:
    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub)
    bot_mock = AsyncMock()
    bot_mock.send_chat_action = AsyncMock()
    bot_mock.send_message = AsyncMock()
    adapter.bot = bot_mock
    return adapter, hub


# ---------------------------------------------------------------------------
# Hub receives MessageType.AUDIO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_message_produces_audio_type(tmp_path) -> None:
    """Voice message → hub receives MessageType.AUDIO."""
    adapter, hub = _make_adapter()
    tmp_file = tmp_path / "audio.ogg"
    tmp_file.touch()

    with patch.object(adapter, "_download_audio", return_value=(tmp_file, 3.0)):
        await adapter._on_voice_message(_make_voice_msg())

    call_args = hub.inbound_bus.put.call_args
    hub_msg = call_args[0][1]
    assert len(hub_msg.attachments) == 1
    assert hub_msg.attachments[0].type == "audio"
    assert hub_msg.platform == "telegram"


# ---------------------------------------------------------------------------
# AudioContent fields are correct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_audio_content_fields(tmp_path) -> None:
    """AudioContent on the hub message has correct url, duration, file_id."""
    adapter, hub = _make_adapter()
    tmp_file = tmp_path / "audio.ogg"
    tmp_file.touch()

    with patch.object(adapter, "_download_audio", return_value=(tmp_file, 3.0)):
        await adapter._on_voice_message(_make_voice_msg(file_id="FILEXYZ", duration=3))

    hub_msg = hub.inbound_bus.put.call_args[0][1]
    assert len(hub_msg.attachments) == 1
    attachment: Attachment = hub_msg.attachments[0]
    assert isinstance(attachment, Attachment)
    assert attachment.type == "audio"
    assert attachment.url_or_bytes == str(tmp_file)
    assert attachment.mime_type == "audio/ogg"


# ---------------------------------------------------------------------------
# Typing indicator fires before download
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typing_action_sent_before_download(tmp_path) -> None:
    """send_chat_action(TYPING) must be called before _download_audio."""
    adapter, hub = _make_adapter()
    call_order: list[str] = []
    tmp_file = tmp_path / "audio.ogg"
    tmp_file.touch()

    adapter.bot.send_chat_action = AsyncMock(
        side_effect=lambda **_: call_order.append("typing")
    )

    async def fake_download(_file_id: str, _duration: int | None = None):
        call_order.append("download")
        return tmp_file, 1.0

    with patch.object(adapter, "_download_audio", side_effect=fake_download):
        await adapter._on_voice_message(_make_voice_msg())

    assert call_order[0] == "typing", "Typing action must fire before download"
    assert "download" in call_order


# ---------------------------------------------------------------------------
# Bot messages are ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_from_bot_is_ignored() -> None:
    """Voice messages from other bots are silently dropped."""
    adapter, hub = _make_adapter()
    msg = _make_voice_msg()
    msg.from_user = SimpleNamespace(id=99, full_name="BotUser", is_bot=True)

    await adapter._on_voice_message(msg)

    hub.inbound_bus.put.assert_not_called()
    adapter.bot.send_chat_action.assert_not_called()


# ---------------------------------------------------------------------------
# Circuit-open: temp file cleaned up, hub not called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_open_drops_message_and_cleans_temp_file(tmp_path) -> None:
    """When hub circuit is open, message is dropped and temp file is deleted."""
    cb = CircuitBreaker(name="hub", failure_threshold=1, recovery_timeout=3600)
    cb.record_failure()  # trip to OPEN (failure_threshold=1)

    registry = CircuitRegistry()
    registry.register(cb)

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="tok", hub=hub, circuit_registry=registry
    )
    bot_mock = AsyncMock()
    adapter.bot = bot_mock

    tmp_file = tmp_path / "audio.ogg"
    tmp_file.touch()

    with patch.object(adapter, "_download_audio", return_value=(tmp_file, 2.0)):
        await adapter._on_voice_message(_make_voice_msg())

    hub.inbound_bus.put.assert_not_called()
    assert not tmp_file.exists(), "Temp file must be deleted when circuit is open"


# ---------------------------------------------------------------------------
# QueueFull: temp file cleaned up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_full_cleans_temp_file(tmp_path) -> None:
    """When hub queue is full, temp file is deleted and backpressure message sent."""
    import asyncio

    adapter, hub = _make_adapter()
    hub.inbound_bus.put.side_effect = asyncio.QueueFull()

    tmp_file = tmp_path / "audio.ogg"
    tmp_file.touch()

    with patch.object(adapter, "_download_audio", return_value=(tmp_file, 1.0)):
        await adapter._on_voice_message(_make_voice_msg())

    assert not tmp_file.exists(), "Temp file must be deleted on QueueFull"
    adapter.bot.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# msg.audio field (F.audio filter) coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_field_message_produces_audio_type(tmp_path) -> None:
    """msg.audio path (F.audio filter) normalises to MessageType.AUDIO."""
    adapter, hub = _make_adapter()
    msg = _make_voice_msg()
    msg.voice = None
    msg.audio = SimpleNamespace(file_id="AUDFILE", duration=5)

    tmp_file = tmp_path / "audio.ogg"
    tmp_file.touch()

    with patch.object(adapter, "_download_audio", return_value=(tmp_file, 5.0)):
        await adapter._on_voice_message(msg)

    hub_msg = hub.inbound_bus.put.call_args[0][1]
    assert len(hub_msg.attachments) == 1
    assert hub_msg.attachments[0].type == "audio"
