"""Outbound audio rendering for DiscordAdapter (audio, attachment, stream renderers)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from io import BytesIO
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters._shared import (
    _AUDIO_EXTS,
    DISCORD_MAX_LENGTH,
    buffer_and_render_audio,
    mime_to_ext,
    parse_reply_to_id,
    sanitize_filename,
    truncate_caption,
)
from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    Platform,
)

if TYPE_CHECKING:
    from lyra.adapters.discord_voice import VoiceSessionManager

log = logging.getLogger(__name__)

# Discord IS_VOICE_MESSAGE flag (bit 13)
_VOICE_MESSAGE_FLAG = 8192


async def render_audio(
    msg: OutboundAudio,
    inbound: InboundMessage,
    *,
    bot_id: str,
    resolve_channel: Any,
    http: Any,
) -> None:
    """Send an OutboundAudio envelope as a Discord voice message.

    Converts to OGG/Opus and sends with IS_VOICE_MESSAGE flag so Discord
    renders a proper voice bubble with waveform and inline playback.
    Falls back to regular file attachment if conversion fails.
    """
    if inbound.platform != Platform.DISCORD.value:
        log.error(
            "render_audio() called with non-discord message id=%s",
            inbound.id,
        )
        return

    channel_id: int | None = inbound.platform_meta.get("channel_id")
    if channel_id is None:
        log.error(
            "render_audio: platform_meta missing 'channel_id' for msg id=%s",
            inbound.id,
        )
        return

    thread_id: int | None = inbound.platform_meta.get("thread_id")
    send_to_id = thread_id if thread_id is not None else channel_id

    # Determine message to reply to
    message_id: int | None = inbound.platform_meta.get("message_id")
    reply_to_id = parse_reply_to_id(msg.reply_to_id)
    if reply_to_id is None and thread_id is None:
        reply_to_id = message_id

    content = (msg.caption or "")[:DISCORD_MAX_LENGTH]

    # TTS already produces OGG/Opus with duration and waveform pre-computed
    duration_secs = msg.duration_ms / 1000.0 if msg.duration_ms is not None else 0.0
    waveform_b64 = msg.waveform_b64 or ""

    payload: dict[str, Any] = {
        "flags": _VOICE_MESSAGE_FLAG,
        "attachments": [
            {
                "id": "0",
                "filename": "voice.ogg",
                "duration_secs": duration_secs,
                "waveform": waveform_b64,
            }
        ],
    }
    if content:
        payload["content"] = content
    if reply_to_id is not None:
        payload["message_reference"] = {"message_id": str(reply_to_id)}

    voice_file = discord.File(fp=BytesIO(msg.audio_bytes), filename="voice.ogg")
    form = [
        {"name": "payload_json", "value": discord.utils._to_json(payload)},
        {
            "name": "files[0]",
            "value": voice_file.fp,
            "filename": "voice.ogg",
            "content_type": "audio/ogg",
        },
    ]
    route = discord.http.Route(  # type: ignore[attr-defined]
        "POST", "/channels/{channel_id}/messages", channel_id=send_to_id
    )
    try:
        await http.request(route, form=form, files=[voice_file])
        log.info(
            "render_audio: voice message sent (%d bytes OGG, %.1fs) for msg id=%s",
            len(msg.audio_bytes),
            duration_secs,
            inbound.id,
        )
    except Exception:
        log.warning(
            "render_audio: voice message failed — falling back to file attachment",
            exc_info=True,
        )
        messageable = await resolve_channel(send_to_id)
        ext = mime_to_ext(msg.mime_type, _AUDIO_EXTS)
        attachment = discord.File(fp=BytesIO(msg.audio_bytes), filename=f"audio.{ext}")
        await messageable.send(content=content or None, file=attachment)


async def render_attachment(
    msg: OutboundAttachment,
    inbound: InboundMessage,
    *,
    resolve_channel: Any,
    attachment_exts: frozenset[str],
) -> None:
    """Send an OutboundAttachment envelope as a Discord file attachment.

    Wraps data in discord.File and sends via messageable.send() or msg.reply().
    Caption (if set) is passed as message content. Reply and thread routing
    follow the same pattern as render_audio.
    """
    if inbound.platform != Platform.DISCORD.value:
        log.error(
            "render_attachment() called with non-discord message id=%s",
            inbound.id,
        )
        return

    channel_id: int | None = inbound.platform_meta.get("channel_id")
    if channel_id is None:
        log.error(
            "render_attachment: platform_meta missing 'channel_id' for msg id=%s",
            inbound.id,
        )
        return

    thread_id: int | None = inbound.platform_meta.get("thread_id")
    send_to_id = thread_id if thread_id is not None else channel_id
    messageable = await resolve_channel(send_to_id)

    # Derive filename: sanitize explicit name or derive from mime_type.
    if msg.filename:
        filename = sanitize_filename(
            msg.filename,
            attachment_exts,
        )
    else:
        ext = mime_to_ext(msg.mime_type, attachment_exts)
        filename = f"attachment.{ext}"

    buf = BytesIO(msg.data)
    file_obj = discord.File(fp=buf, filename=filename)

    # Determine reply target
    message_id: int | None = inbound.platform_meta.get("message_id")
    reply_to_id = parse_reply_to_id(msg.reply_to_id)
    if reply_to_id is None and thread_id is None:
        reply_to_id = message_id

    content = truncate_caption(msg.caption, DISCORD_MAX_LENGTH) or ""

    if reply_to_id is not None:
        try:
            ref_msg = await messageable.fetch_message(reply_to_id)
            await ref_msg.reply(content=content or None, file=file_obj)
            return
        except Exception:
            log.warning(
                "render_attachment: could not reply to message_id=%s, sending normally",
                reply_to_id,
            )

    # Fallback: construct fresh discord.File (previous BytesIO may be consumed).
    file_obj = discord.File(fp=BytesIO(msg.data), filename=filename)
    await messageable.send(content=content or None, file=file_obj)


async def render_audio_stream(
    chunks: AsyncIterator[OutboundAudioChunk],
    inbound: InboundMessage,
    render_audio_fn: Callable[[OutboundAudio, InboundMessage], Awaitable[None]],
) -> None:
    """Buffer streamed audio chunks and send as a single Discord file attachment."""
    if inbound.platform != Platform.DISCORD.value:
        log.error(
            "render_audio_stream() called with non-discord message id=%s",
            inbound.id,
        )
        return
    await buffer_and_render_audio(chunks, inbound, render_audio_fn)


async def render_voice_stream(
    chunks: AsyncIterator[OutboundAudioChunk],
    inbound: InboundMessage,
    vsm: "VoiceSessionManager",
) -> None:
    """Route TTS stream to the active Discord voice session for this guild."""
    if inbound.platform != Platform.DISCORD.value:
        log.warning(
            "render_voice_stream() called with non-discord message id=%s",
            inbound.id,
        )
        return
    guild_id = inbound.platform_meta.get("guild_id")
    if guild_id is None:
        log.warning(
            "render_voice_stream: platform_meta missing 'guild_id' for msg id=%s",
            inbound.id,
        )
        return
    await vsm.stream(str(guild_id), chunks)
