"""Tests for Telegram voice message handling and normalize_audio.

Issues: #147, #140, #173.

Covers:
- Voice message → download + enqueue on inbound audio bus
- Bot message drops voice messages from bots
- normalize_audio envelope correctness
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.adapters.telegram import _ALLOW_ALL, TelegramAdapter


def _make_voice_msg(  # noqa: PLR0913 — test factory with optional overrides
    file_id: str = "FILE123",
    duration: int = 3,
    chat_id: int = 42,
    user_id: int = 7,
    chat_type: str = "private",
    topic_id: int | None = None,
    message_id: int | None = 55,
) -> SimpleNamespace:
    """Build a minimal aiogram-like voice message stub."""
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=chat_type),
        from_user=SimpleNamespace(id=user_id, full_name="Alice", is_bot=False),
        voice=SimpleNamespace(file_id=file_id, duration=duration),
        audio=None,
        date=datetime.now(timezone.utc),
        message_thread_id=topic_id,
        message_id=message_id,
    )


def _make_adapter() -> tuple[TelegramAdapter, MagicMock]:
    inbound_bus = MagicMock()
    inbound_bus.put = AsyncMock()
    buses = MagicMock()
    buses.inbound_bus = inbound_bus
    adapter = TelegramAdapter(
        bot_id="main",
        token="tok",
        inbound_bus=inbound_bus,
        auth=_ALLOW_ALL,
    )
    bot_mock = AsyncMock()
    bot_mock.send_chat_action = AsyncMock()
    bot_mock.send_message = AsyncMock()
    adapter.bot = bot_mock
    return adapter, buses


# ---------------------------------------------------------------------------
# Voice message → download + enqueue on audio bus (#173)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_message_enqueues_on_audio_bus(tmp_path: Path) -> None:
    """Voice message → downloads audio and enqueues on inbound_bus."""
    adapter, buses = _make_adapter()

    # Create a temp file to simulate download
    audio_file = tmp_path / "voice.ogg"
    audio_file.write_bytes(b"fake_ogg_data")

    with patch(
        "lyra.adapters.telegram_inbound._download_audio",
        new_callable=AsyncMock,
        return_value=(audio_file, 3.0),
    ):
        await adapter._on_voice_message(_make_voice_msg())

    # Enqueued on unified inbound bus
    buses.inbound_bus.put.assert_called_once()
    # No unsupported reply sent
    adapter.bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Bot messages are ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_from_bot_is_ignored() -> None:
    """Voice messages from other bots are silently dropped."""
    adapter, buses = _make_adapter()
    msg = _make_voice_msg()
    msg.from_user = SimpleNamespace(id=99, full_name="BotUser", is_bot=True)

    await adapter._on_voice_message(msg)

    buses.inbound_bus.put.assert_not_called()
    adapter.bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Error paths (#173)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_message_no_voice_object_returns_early() -> None:
    """Message with voice=None, audio=None, video_note=None → early return."""
    adapter, buses = _make_adapter()
    msg = _make_voice_msg()
    msg.voice = None
    msg.audio = None

    await adapter._on_voice_message(msg)

    buses.inbound_bus.put.assert_not_called()
    adapter.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_voice_message_no_file_id_returns_early() -> None:
    """Voice object with file_id=None → early return."""
    adapter, buses = _make_adapter()
    msg = _make_voice_msg()
    msg.voice = SimpleNamespace(file_id=None, duration=3)

    await adapter._on_voice_message(msg)

    buses.inbound_bus.put.assert_not_called()
    adapter.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_voice_message_too_large_sends_reply() -> None:
    """_download_audio raises ValueError → user gets 'too large' reply."""
    adapter, buses = _make_adapter()

    with patch(
        "lyra.adapters.telegram_inbound._download_audio",
        new_callable=AsyncMock,
        side_effect=ValueError("Audio file too large"),
    ):
        await adapter._on_voice_message(_make_voice_msg())

    buses.inbound_bus.put.assert_not_called()
    adapter.bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_voice_too_large_replies_to_original_message() -> None:
    """Audio-too-large error replies to the original message (mirrors Discord UX)."""
    adapter, _hub = _make_adapter()

    with patch(
        "lyra.adapters.telegram_inbound._download_audio",
        new_callable=AsyncMock,
        side_effect=ValueError("too large"),
    ):
        await adapter._on_voice_message(_make_voice_msg(message_id=55))

    call_kwargs = adapter.bot.send_message.call_args.kwargs
    assert call_kwargs.get("reply_to_message_id") == 55


@pytest.mark.asyncio
async def test_voice_too_large_no_reply_when_no_message_id() -> None:
    """Audio-too-large error omits reply_to_message_id when message_id is None."""
    adapter, _hub = _make_adapter()

    with patch(
        "lyra.adapters.telegram_inbound._download_audio",
        new_callable=AsyncMock,
        side_effect=ValueError("too large"),
    ):
        await adapter._on_voice_message(_make_voice_msg(message_id=None))

    call_kwargs = adapter.bot.send_message.call_args.kwargs
    assert "reply_to_message_id" not in call_kwargs


@pytest.mark.asyncio
async def test_voice_message_download_error_returns_silently() -> None:
    """_download_audio raises generic exception → log + return, no enqueue."""
    adapter, buses = _make_adapter()

    with patch(
        "lyra.adapters.telegram_inbound._download_audio",
        new_callable=AsyncMock,
        side_effect=RuntimeError("network error"),
    ):
        await adapter._on_voice_message(_make_voice_msg())

    buses.inbound_bus.put.assert_not_called()
    adapter.bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# normalize_audio() — issue #140
# ---------------------------------------------------------------------------


def test_normalize_audio_voice_fields() -> None:
    """normalize_audio returns InboundMessage(modality='voice') with correct fields."""
    from lyra.core.audio_payload import AudioPayload
    from lyra.core.message import InboundMessage
    from lyra.core.trust import TrustLevel

    adapter, _ = _make_adapter()
    msg = _make_voice_msg(file_id="F1", duration=3, chat_id=42, user_id=7)

    result = adapter.normalize_audio(
        msg, b"data", "audio/ogg", trust_level=TrustLevel.TRUSTED
    )
    assert isinstance(result, InboundMessage)
    assert result.modality == "voice"
    assert result.id.startswith("telegram:tg:user:7:")
    assert result.scope_id == "chat:42"
    assert result.user_id == "tg:user:7"
    assert result.platform == "telegram"
    assert result.bot_id == "main"
    assert result.trust == "user"
    assert isinstance(result.audio, AudioPayload)
    assert result.audio.mime_type == "audio/ogg"
    assert result.audio.duration_ms == 3000
    assert result.audio.file_id == "F1"
    assert result.audio.audio_bytes == b"data"


def test_normalize_audio_audio_file_fields() -> None:
    """normalize_audio reads mime_type and duration from msg.audio when voice is None."""  # noqa: E501
    from lyra.core.audio_payload import AudioPayload
    from lyra.core.message import InboundMessage
    from lyra.core.trust import TrustLevel

    adapter, _ = _make_adapter()
    msg = _make_voice_msg(file_id="AF1", duration=5, chat_id=99, user_id=8)
    msg.voice = None
    msg.audio = SimpleNamespace(file_id="AF1", duration=5, mime_type="audio/mpeg")

    result = adapter.normalize_audio(
        msg, b"bytes", "audio/mpeg", trust_level=TrustLevel.TRUSTED
    )
    assert isinstance(result, InboundMessage)
    assert result.modality == "voice"
    assert result.trust == "user"
    assert isinstance(result.audio, AudioPayload)
    assert result.audio.mime_type == "audio/mpeg"
    assert result.audio.duration_ms == 5000
    assert result.audio.file_id == "AF1"


def test_normalize_audio_private_chat_scope_id() -> None:
    """Private chat → scope_id='chat:<id>'."""
    from lyra.core.message import InboundMessage
    from lyra.core.trust import TrustLevel

    adapter, _ = _make_adapter()
    msg = _make_voice_msg(chat_id=42, chat_type="private")
    result = adapter.normalize_audio(
        msg, b"x", "audio/ogg", trust_level=TrustLevel.TRUSTED
    )
    assert isinstance(result, InboundMessage)
    assert result.scope_id == "chat:42"


def test_normalize_audio_group_chat_user_scoped_scope_id() -> None:
    """Group chat (no topic) → scope_id includes user suffix (#356)."""
    from lyra.core.message import InboundMessage
    from lyra.core.trust import TrustLevel

    adapter, _ = _make_adapter()
    msg = _make_voice_msg(chat_id=42, chat_type="group")
    result = adapter.normalize_audio(
        msg, b"x", "audio/ogg", trust_level=TrustLevel.TRUSTED
    )
    assert isinstance(result, InboundMessage)
    assert result.scope_id == "chat:42:user:tg:user:7"


def test_normalize_audio_topic_chat_scope_id() -> None:
    """Topic chat → scope_id includes topic AND user suffix (#356)."""
    from lyra.core.message import InboundMessage
    from lyra.core.trust import TrustLevel

    adapter, _ = _make_adapter()
    msg = _make_voice_msg(chat_id=42, topic_id=7, user_id=99, chat_type="supergroup")
    result = adapter.normalize_audio(
        msg, b"x", "audio/ogg", trust_level=TrustLevel.TRUSTED
    )
    assert isinstance(result, InboundMessage)
    assert result.scope_id == "chat:42:topic:7:user:tg:user:99"


# ---------------------------------------------------------------------------
# video_note mime_type (B2 fix) — video/mp4, not audio/ogg
# ---------------------------------------------------------------------------


def test_normalize_audio_video_note_fields() -> None:
    """video_note messages produce correct mime_type and fields."""
    from lyra.core.audio_payload import AudioPayload
    from lyra.core.message import InboundMessage
    from lyra.core.trust import TrustLevel

    adapter, _ = _make_adapter()
    msg = _make_voice_msg()
    msg.voice = None
    msg.audio = None
    msg.video_note = SimpleNamespace(file_id="VN123", duration=5)

    result = adapter.normalize_audio(
        msg, b"vid", "video/mp4", trust_level=TrustLevel.TRUSTED
    )
    assert isinstance(result, InboundMessage)
    assert result.modality == "voice"
    assert isinstance(result.audio, AudioPayload)
    assert result.audio.mime_type == "video/mp4"
    assert result.audio.duration_ms == 5000
    assert result.audio.file_id == "VN123"
