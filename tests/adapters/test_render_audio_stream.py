"""Tests for render_audio_stream() on Telegram and Discord adapters (issue #144).

Covers:
- Single-chunk stream (is_final=True on first chunk) sends audio
- Multi-chunk stream buffers all chunks, sends once on is_final
- Empty stream (no chunks) results in no send call
- Error mid-stream sends partial buffer then re-raises
- caption and reply_to_id from final chunk are used
- Discord fetch-failure fallback
- Discord no-reply-target path
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import BufferedInputFile

from lyra.core.auth import TrustLevel
from lyra.core.message import (
    InboundMessage,
    OutboundAudioChunk,
)

from .conftest import (
    make_dc_adapter,
    make_dc_msg,
    make_tg_adapter,
    make_tg_msg,
    mock_channel,
)

# ---------------------------------------------------------------------------
# Stream fixtures
# ---------------------------------------------------------------------------


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
    yield OutboundAudioChunk(chunk_bytes=b"partial1", session_id="s1", chunk_index=0)
    yield OutboundAudioChunk(chunk_bytes=b"partial2", session_id="s1", chunk_index=1)
    raise RuntimeError("TTS pipeline crashed")


# ---------------------------------------------------------------------------
# TelegramAdapter.render_audio_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_single_chunk_sends_voice() -> None:
    adapter = make_tg_adapter()
    inbound = make_tg_msg()

    await adapter.render_audio_stream(_single_chunk(), inbound)

    adapter.bot.send_voice.assert_awaited_once()
    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["chat_id"] == 42


@pytest.mark.asyncio
async def test_tg_multi_chunk_buffers_and_sends_once() -> None:
    adapter = make_tg_adapter()
    inbound = make_tg_msg()

    await adapter.render_audio_stream(_multi_chunk(), inbound)

    adapter.bot.send_voice.assert_awaited_once()
    kwargs = adapter.bot.send_voice.call_args.kwargs
    voice_data = kwargs["voice"]
    assert isinstance(voice_data, BufferedInputFile)
    assert voice_data.data == b"chunk0chunk1chunk2"


@pytest.mark.asyncio
async def test_tg_multi_chunk_uses_final_caption() -> None:
    adapter = make_tg_adapter()
    inbound = make_tg_msg()

    await adapter.render_audio_stream(_multi_chunk(), inbound)

    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["caption"] == "final caption"


@pytest.mark.asyncio
async def test_tg_multi_chunk_uses_final_reply_to_id() -> None:
    adapter = make_tg_adapter()
    inbound = make_tg_msg()

    await adapter.render_audio_stream(_multi_chunk(), inbound)

    kwargs = adapter.bot.send_voice.call_args.kwargs
    assert kwargs["reply_to_message_id"] == 200


@pytest.mark.asyncio
async def test_tg_empty_stream_no_send() -> None:
    adapter = make_tg_adapter()
    inbound = make_tg_msg()

    await adapter.render_audio_stream(_empty_stream(), inbound)

    adapter.bot.send_voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_tg_error_mid_stream_sends_partial_then_raises(caplog) -> None:
    adapter = make_tg_adapter()
    inbound = make_tg_msg()

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="TTS pipeline crashed"):
            await adapter.render_audio_stream(_error_stream(), inbound)

    # Partial buffer was sent before re-raise
    adapter.bot.send_voice.assert_awaited_once()
    kwargs = adapter.bot.send_voice.call_args.kwargs
    voice_data = kwargs["voice"]
    assert isinstance(voice_data, BufferedInputFile)
    assert voice_data.data == b"partial1partial2"
    assert "Audio stream interrupted" in caplog.text


@pytest.mark.asyncio
async def test_tg_wrong_platform_no_send() -> None:
    adapter = make_tg_adapter()
    inbound = make_dc_msg()  # wrong platform

    await adapter.render_audio_stream(_single_chunk(), inbound)

    adapter.bot.send_voice.assert_not_awaited()


# ---------------------------------------------------------------------------
# DiscordAdapter.render_audio_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_single_chunk_sends_file() -> None:
    adapter = make_dc_adapter()
    channel = mock_channel()
    inbound = make_dc_msg()

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio_stream(_single_chunk(), inbound)

    channel.send.assert_awaited_once()
    import discord as _discord

    call_kwargs = channel.send.call_args.kwargs
    assert isinstance(call_kwargs["file"], _discord.File)
    # Verify file content
    assert call_kwargs["file"].fp.read() == b"OGG_DATA"


@pytest.mark.asyncio
async def test_dc_multi_chunk_buffers_and_sends_once() -> None:
    adapter = make_dc_adapter()
    channel = mock_channel()
    inbound = make_dc_msg()

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio_stream(_multi_chunk(), inbound)

    channel.send.assert_awaited_once()
    call_kwargs = channel.send.call_args.kwargs
    assert call_kwargs["content"] == "final caption"
    # Verify buffered bytes
    assert call_kwargs["file"].fp.read() == b"chunk0chunk1chunk2"


@pytest.mark.asyncio
async def test_dc_empty_stream_no_send() -> None:
    adapter = make_dc_adapter()
    channel = mock_channel()
    inbound = make_dc_msg()

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio_stream(_empty_stream(), inbound)

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_dc_error_mid_stream_sends_partial_then_raises(caplog) -> None:
    adapter = make_dc_adapter()
    channel = mock_channel()
    inbound = make_dc_msg()

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="TTS pipeline crashed"):
            with patch.object(adapter, "get_channel", return_value=channel):
                await adapter.render_audio_stream(_error_stream(), inbound)

    channel.send.assert_awaited_once()
    # Verify partial bytes
    call_kwargs = channel.send.call_args.kwargs
    assert call_kwargs["file"].fp.read() == b"partial1partial2"
    assert "Audio stream interrupted" in caplog.text


@pytest.mark.asyncio
async def test_dc_wrong_platform_no_send() -> None:
    adapter = make_dc_adapter()
    channel = mock_channel()
    inbound = make_tg_msg()  # wrong platform

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio_stream(_single_chunk(), inbound)

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_dc_stream_fallback_to_send_on_fetch_failure() -> None:
    """When fetch_message raises, fallback to channel.send."""
    adapter = make_dc_adapter()
    channel = mock_channel()
    channel.fetch_message = AsyncMock(side_effect=Exception("not found"))
    inbound = make_dc_msg()

    with patch.object(adapter, "get_channel", return_value=channel):
        await adapter.render_audio_stream(_single_chunk(), inbound)

    channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_dc_stream_no_reply_target_sends_normally() -> None:
    """When message_id is None, send without reply."""
    adapter = make_dc_adapter()
    channel = mock_channel()
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
        trust_level=TrustLevel.TRUSTED,
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
        await adapter.render_audio_stream(_single_chunk(), inbound)

    # message_id=None means no reply attempted, send directly
    channel.send.assert_awaited_once()
