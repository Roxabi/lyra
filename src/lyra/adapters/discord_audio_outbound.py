"""Outbound audio rendering for DiscordAdapter (audio, attachment, stream renderers)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from io import BytesIO
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters._shared import (
    _AUDIO_EXTS,
    ATTACHMENT_EXTS_BASE,
    DISCORD_MAX_LENGTH,
    buffer_and_render_audio,
    mime_to_ext,
    parse_reply_to_id,
    sanitize_filename,
    truncate_caption,
)
from lyra.adapters.discord_formatting import _validate_inbound
from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    Platform,
)

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger(__name__)

# Discord IS_VOICE_MESSAGE flag (bit 13)
_VOICE_MESSAGE_FLAG = 8192


async def render_audio(
    adapter: "DiscordAdapter",
    msg: OutboundAudio,
    inbound: InboundMessage,
) -> None:
    """Send an OutboundAudio envelope as a Discord voice message.

    Converts to OGG/Opus and sends with IS_VOICE_MESSAGE flag so Discord
    renders a proper voice bubble with waveform and inline playback.
    Falls back to regular file attachment if conversion fails.
    """
    meta = _validate_inbound(inbound, "render_audio")
    if meta is None:
        return
    channel_id, thread_id, message_id = meta
    send_to_id = thread_id if thread_id is not None else channel_id

    # Determine message to reply to
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
        await adapter.http.request(route, form=form, files=[voice_file])
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
        messageable = await adapter._resolve_channel(send_to_id)
        ext = mime_to_ext(msg.mime_type, _AUDIO_EXTS)
        attachment = discord.File(fp=BytesIO(msg.audio_bytes), filename=f"audio.{ext}")
        await messageable.send(content=content or None, file=attachment)


async def render_attachment(
    adapter: "DiscordAdapter",
    msg: OutboundAttachment,
    inbound: InboundMessage,
) -> None:
    """Send an OutboundAttachment envelope as a Discord file attachment.

    Wraps data in discord.File and sends via messageable.send() or msg.reply().
    Caption (if set) is passed as message content. Reply and thread routing
    follow the same pattern as render_audio.
    """
    meta = _validate_inbound(inbound, "render_attachment")
    if meta is None:
        return
    channel_id, thread_id, message_id = meta
    send_to_id = thread_id if thread_id is not None else channel_id
    messageable = await adapter._resolve_channel(send_to_id)

    # Derive filename: sanitize explicit name or derive from mime_type.
    if msg.filename:
        filename = sanitize_filename(
            msg.filename,
            ATTACHMENT_EXTS_BASE,
        )
    else:
        ext = mime_to_ext(msg.mime_type, ATTACHMENT_EXTS_BASE)
        filename = f"attachment.{ext}"

    buf = BytesIO(msg.data)
    file_obj = discord.File(fp=buf, filename=filename)

    # Determine reply target
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
    adapter: "DiscordAdapter",
    chunks: AsyncIterator[OutboundAudioChunk],
    inbound: InboundMessage,
) -> None:
    """Buffer streamed audio chunks and send as a single Discord file attachment."""
    # Guard: short-circuit before consuming chunks (render_audio re-validates).
    meta = _validate_inbound(inbound, "render_audio_stream")
    if meta is None:
        return
    await buffer_and_render_audio(
        chunks, inbound, lambda audio, msg: render_audio(adapter, audio, msg)
    )


async def render_voice_stream(
    adapter: "DiscordAdapter",
    chunks: AsyncIterator[OutboundAudioChunk],
    inbound: InboundMessage,
) -> None:
    """Route TTS stream to the active Discord voice session for this guild.

    Uses platform-only check (not _validate_inbound) because voice streams
    route by guild_id, not channel_id.
    """
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
    await adapter._vsm.stream(str(guild_id), chunks)
