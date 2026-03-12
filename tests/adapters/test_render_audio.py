"""Tests for render_audio() on Telegram and Discord adapters (issue #141).

Covers:
- TelegramAdapter.render_audio() calls bot.send_voice with correct kwargs
- reply_to_message_id is derived from inbound.platform_meta["message_id"]
- explicit msg.reply_to_id overrides the default
- caption and duration_ms are forwarded correctly
- non-telegram platform logs an error and returns without sending
- DiscordAdapter.render_audio() sends audio as discord.File attachment
- caption is passed as message content
- reply falls back to channel.send on fetch failure
- non-discord platform logs an error and returns without sending
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.core.auth import TrustLevel
from lyra.core.message import (
    InboundMessage,
    OutboundAudio,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tg_msg(
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
        platform_meta={
            "chat_id": chat_id,
            "message_id": message_id,
            "topic_id": topic_id,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def _dc_msg(channel_id: int = 99, message_id: int = 55) -> InboundMessage:
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
        platform_meta={
            "guild_id": 1,
            "channel_id": channel_id,
            "message_id": message_id,
            "thread_id": None,
            "channel_type": "text",
        },
        trust_level=TrustLevel.TRUSTED,
    )


def _make_tg_adapter() -> TelegramAdapter:
    hub = MagicMock()
    adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub)
    bot_mock = AsyncMock()
    bot_mock.send_voice = AsyncMock()
    adapter.bot = bot_mock
    return adapter


def _make_dc_adapter() -> DiscordAdapter:
    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main")
    return adapter


# ---------------------------------------------------------------------------
# TelegramAdapter.render_audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_render_audio_calls_send_voice() -> None:
    adapter = _make_tg_adapter()
    audio = OutboundAudio(audio_bytes=b"OGG", mime_type="audio/ogg")
    inbound = _tg_msg()

    await adapter.render_audio(audio, inbound)

    adapter.bot.send_voice.assert_awaited_once()
    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["chat_id"] == 42
    assert isinstance(kwargs["voice"], BytesIO)
    assert kwargs["voice"].read() == b"OGG"


@pytest.mark.asyncio
async def test_tg_render_audio_default_reply_to_message_id() -> None:
    adapter = _make_tg_adapter()
    audio = OutboundAudio(audio_bytes=b"OGG")
    inbound = _tg_msg(message_id=77)

    await adapter.render_audio(audio, inbound)

    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["reply_to_message_id"] == 77


@pytest.mark.asyncio
async def test_tg_render_audio_explicit_reply_to_id_overrides() -> None:
    adapter = _make_tg_adapter()
    audio = OutboundAudio(audio_bytes=b"OGG", reply_to_id="200")
    inbound = _tg_msg(message_id=77)

    await adapter.render_audio(audio, inbound)

    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["reply_to_message_id"] == 200


@pytest.mark.asyncio
async def test_tg_render_audio_caption_forwarded() -> None:
    adapter = _make_tg_adapter()
    audio = OutboundAudio(audio_bytes=b"OGG", caption="Lyra speaking")
    inbound = _tg_msg()

    await adapter.render_audio(audio, inbound)

    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["caption"] == "Lyra speaking"


@pytest.mark.asyncio
async def test_tg_render_audio_duration_converted_to_seconds() -> None:
    adapter = _make_tg_adapter()
    audio = OutboundAudio(audio_bytes=b"OGG", duration_ms=3500)
    inbound = _tg_msg()

    await adapter.render_audio(audio, inbound)

    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["duration"] == 3  # floor division


@pytest.mark.asyncio
async def test_tg_render_audio_topic_thread_id_forwarded() -> None:
    adapter = _make_tg_adapter()
    audio = OutboundAudio(audio_bytes=b"OGG")
    inbound = _tg_msg(topic_id=5)

    await adapter.render_audio(audio, inbound)

    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["message_thread_id"] == 5


@pytest.mark.asyncio
async def test_tg_render_audio_non_telegram_context_no_send(caplog) -> None:
    adapter = _make_tg_adapter()
    audio = OutboundAudio(audio_bytes=b"OGG")
    inbound = _dc_msg()  # wrong platform

    await adapter.render_audio(audio, inbound)

    adapter.bot.send_voice.assert_not_awaited()


# ---------------------------------------------------------------------------
# DiscordAdapter.render_audio
# ---------------------------------------------------------------------------


def _mock_channel() -> MagicMock:
    ch = AsyncMock()
    ch.send = AsyncMock()
    return ch


@pytest.mark.asyncio
async def test_dc_render_audio_sends_file_attachment() -> None:
    adapter = _make_dc_adapter()
    channel = _mock_channel()
    ref_msg = AsyncMock()
    ref_msg.reply = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=ref_msg)

    audio = OutboundAudio(audio_bytes=b"MP3", mime_type="audio/mpeg")
    inbound = _dc_msg(channel_id=99, message_id=55)

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio(audio, inbound)

    ref_msg.reply.assert_awaited_once()
    call_kwargs = ref_msg.reply.call_args.kwargs
    import discord as _discord

    assert isinstance(call_kwargs["file"], _discord.File)
    assert call_kwargs["file"].filename == "audio.mpeg"


@pytest.mark.asyncio
async def test_dc_render_audio_caption_as_content() -> None:
    adapter = _make_dc_adapter()
    channel = _mock_channel()
    ref_msg = AsyncMock()
    ref_msg.reply = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=ref_msg)

    audio = OutboundAudio(audio_bytes=b"OGG", caption="Hello from Lyra")
    inbound = _dc_msg()

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio(audio, inbound)

    call_kwargs = ref_msg.reply.call_args.kwargs
    assert call_kwargs["content"] == "Hello from Lyra"


@pytest.mark.asyncio
async def test_dc_render_audio_fallback_to_send_on_fetch_failure() -> None:
    adapter = _make_dc_adapter()
    channel = _mock_channel()
    channel.fetch_message = AsyncMock(side_effect=Exception("not found"))

    audio = OutboundAudio(audio_bytes=b"OGG")
    inbound = _dc_msg()

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio(audio, inbound)

    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_dc_render_audio_no_reply_to_id_sends_normally() -> None:
    """When reply_to_id is None and message_id is None, send without reply."""
    adapter = _make_dc_adapter()
    channel = _mock_channel()

    audio = OutboundAudio(audio_bytes=b"OGG", reply_to_id=None)
    # Use message_id=None to simulate no reply target
    inbound = InboundMessage(
        id="discord:dc:user:1:0:0",
        platform="discord",
        bot_id="main",
        scope_id="channel:99",
        user_id="dc:user:1",
        user_name="Bob",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "guild_id": 1,
            "channel_id": 99,
            "message_id": None,
            "thread_id": None,
            "channel_type": "text",
        },
    )

    ref_msg = AsyncMock()
    ref_msg.reply = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=ref_msg)

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio(audio, inbound)

    # message_id=None means no reply attempted, send directly
    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_dc_render_audio_non_discord_context_no_send(caplog) -> None:
    adapter = _make_dc_adapter()
    channel = _mock_channel()
    audio = OutboundAudio(audio_bytes=b"OGG")
    inbound = _tg_msg()  # wrong platform

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio(audio, inbound)

    channel.send.assert_not_awaited()
    channel.fetch_message.assert_not_called()
