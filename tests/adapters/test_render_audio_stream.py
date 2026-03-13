"""Tests for render_audio_stream() on Telegram and Discord adapters (issue #144).

Covers:
- Single-chunk stream (is_final=True on first chunk) sends audio
- Multi-chunk stream buffers all chunks, sends once on is_final
- Empty stream (no chunks) results in no send call
- Error mid-stream sends partial buffer with warning log
- caption and reply_to_id from final chunk are used
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.core.auth import TrustLevel
from lyra.core.message import (
    InboundMessage,
    OutboundAudioChunk,
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


def _mock_channel() -> MagicMock:
    ch = AsyncMock()
    ch.send = AsyncMock()
    return ch


async def _single_chunk(
    data: bytes = b"OGG_DATA",
    caption: str | None = None,
    reply_to_id: str | None = None,
) -> AsyncIterator[OutboundAudioChunk]:
    yield OutboundAudioChunk(
        chunk_bytes=data,
        session_id="s1",
        chunk_index=0,
        is_final=True,
        caption=caption,
        reply_to_id=reply_to_id,
    )


async def _multi_chunk() -> AsyncIterator[OutboundAudioChunk]:
    for i in range(3):
        yield OutboundAudioChunk(
            chunk_bytes=f"chunk{i}".encode(),
            session_id="s1",
            chunk_index=i,
            is_final=(i == 2),
            caption="final caption" if i == 2 else None,
            reply_to_id="200" if i == 2 else None,
        )


async def _empty_stream() -> AsyncIterator[OutboundAudioChunk]:
    return
    yield  # type: ignore[misc]  # make it an async generator


async def _error_stream() -> AsyncIterator[OutboundAudioChunk]:
    yield OutboundAudioChunk(
        chunk_bytes=b"partial1", session_id="s1", chunk_index=0
    )
    yield OutboundAudioChunk(
        chunk_bytes=b"partial2", session_id="s1", chunk_index=1
    )
    raise RuntimeError("TTS pipeline crashed")


# ---------------------------------------------------------------------------
# TelegramAdapter.render_audio_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_single_chunk_sends_voice() -> None:
    adapter = _make_tg_adapter()
    inbound = _tg_msg()

    await adapter.render_audio_stream(_single_chunk(), inbound)

    adapter.bot.send_voice.assert_awaited_once()
    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["chat_id"] == 42


@pytest.mark.asyncio
async def test_tg_multi_chunk_buffers_and_sends_once() -> None:
    adapter = _make_tg_adapter()
    inbound = _tg_msg()

    await adapter.render_audio_stream(_multi_chunk(), inbound)

    adapter.bot.send_voice.assert_awaited_once()
    kwargs = adapter.bot.send_voice.call_args.kwargs
    # All 3 chunks concatenated
    from io import BytesIO

    voice_data = kwargs["voice"]
    assert isinstance(voice_data, BytesIO)
    assert voice_data.read() == b"chunk0chunk1chunk2"


@pytest.mark.asyncio
async def test_tg_multi_chunk_uses_final_caption() -> None:
    adapter = _make_tg_adapter()
    inbound = _tg_msg()

    await adapter.render_audio_stream(_multi_chunk(), inbound)

    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["caption"] == "final caption"


@pytest.mark.asyncio
async def test_tg_multi_chunk_uses_final_reply_to_id() -> None:
    adapter = _make_tg_adapter()
    inbound = _tg_msg()

    await adapter.render_audio_stream(_multi_chunk(), inbound)

    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["reply_to_message_id"] == 200


@pytest.mark.asyncio
async def test_tg_empty_stream_no_send() -> None:
    adapter = _make_tg_adapter()
    inbound = _tg_msg()

    await adapter.render_audio_stream(_empty_stream(), inbound)

    adapter.bot.send_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_tg_error_mid_stream_sends_partial(caplog) -> None:
    adapter = _make_tg_adapter()
    inbound = _tg_msg()

    with caplog.at_level(logging.WARNING):
        await adapter.render_audio_stream(_error_stream(), inbound)

    adapter.bot.send_voice.assert_awaited_once()
    kwargs = adapter.bot.send_voice.call_args.kwargs
    from io import BytesIO

    voice_data = kwargs["voice"]
    assert isinstance(voice_data, BytesIO)
    assert voice_data.read() == b"partial1partial2"
    assert "Audio stream interrupted" in caplog.text


@pytest.mark.asyncio
async def test_tg_wrong_platform_no_send() -> None:
    adapter = _make_tg_adapter()
    inbound = _dc_msg()  # wrong platform

    await adapter.render_audio_stream(_single_chunk(), inbound)

    adapter.bot.send_voice.assert_not_awaited()


# ---------------------------------------------------------------------------
# DiscordAdapter.render_audio_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_single_chunk_sends_file() -> None:
    adapter = _make_dc_adapter()
    channel = _mock_channel()
    ref_msg = AsyncMock()
    ref_msg.reply = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=ref_msg)
    inbound = _dc_msg()

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio_stream(_single_chunk(), inbound)

    ref_msg.reply.assert_awaited_once()
    import discord as _discord

    call_kwargs = ref_msg.reply.call_args.kwargs
    assert isinstance(call_kwargs["file"], _discord.File)


@pytest.mark.asyncio
async def test_dc_multi_chunk_buffers_and_sends_once() -> None:
    adapter = _make_dc_adapter()
    channel = _mock_channel()
    ref_msg = AsyncMock()
    ref_msg.reply = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=ref_msg)
    inbound = _dc_msg()

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio_stream(_multi_chunk(), inbound)

    ref_msg.reply.assert_awaited_once()
    call_kwargs = ref_msg.reply.call_args.kwargs
    assert call_kwargs["content"] == "final caption"


@pytest.mark.asyncio
async def test_dc_empty_stream_no_send() -> None:
    adapter = _make_dc_adapter()
    channel = _mock_channel()
    inbound = _dc_msg()

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio_stream(_empty_stream(), inbound)

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_dc_error_mid_stream_sends_partial(caplog) -> None:
    adapter = _make_dc_adapter()
    channel = _mock_channel()
    ref_msg = AsyncMock()
    ref_msg.reply = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=ref_msg)
    inbound = _dc_msg()

    with caplog.at_level(logging.WARNING):
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_audio_stream(_error_stream(), inbound)

    ref_msg.reply.assert_awaited_once()
    assert "Audio stream interrupted" in caplog.text


@pytest.mark.asyncio
async def test_dc_wrong_platform_no_send() -> None:
    adapter = _make_dc_adapter()
    channel = _mock_channel()
    inbound = _tg_msg()  # wrong platform

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio_stream(_single_chunk(), inbound)

    channel.send.assert_not_awaited()
