"""Audio detection, normalization, and rendering for DiscordAdapter."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from io import BytesIO
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters._shared import (
    _AUDIO_EXTS,
    DISCORD_MAX_LENGTH,
    _PartialAudioError,
    buffer_audio_chunks,
    parse_reply_to_id,
    sanitize_filename,
    truncate_caption,
)
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    Platform,
    RoutingContext,
)

if TYPE_CHECKING:
    from lyra.adapters.discord_voice import VoiceSessionManager
    from lyra.core.trust import TrustLevel

log = logging.getLogger(__name__)

# Discord IS_VOICE_MESSAGE flag (bit 13)
_VOICE_MESSAGE_FLAG = 8192


def is_valid_audio_magic(data: bytes) -> bool:
    """Return True if *data* starts with a recognised audio file signature.

    Checks magic bytes for common audio containers. The client-supplied
    content_type is untrusted, so this provides a server-side format gate.
    """
    if len(data) < 4:  # noqa: PLR2004 — smallest magic header is 4 bytes
        return False
    # OGG / Opus / Vorbis
    if data[:4] == b"OggS":
        return True
    # WEBM (also used for Opus in browsers / Discord voice)
    if data[:4] == b"\x1aE\xdf\xa3":
        return True
    # RIFF (WAV, WebP — WAV is audio/wav)
    if data[:4] == b"RIFF":
        return True
    # FLAC
    if data[:4] == b"fLaC":
        return True
    # MP3 — ID3 tag header
    if data[:3] == b"ID3":
        return True
    # MP3 — raw sync word (0xff + 0xfb/0xf3/0xf2)
    if data[0] == 0xFF and data[1] in (0xFB, 0xF3, 0xF2, 0xFA):
        return True
    # M4A / MP4 — "ftyp" at offset 4
    if len(data) >= 8 and data[4:8] == b"ftyp":  # noqa: PLR2004
        return True
    return False


def normalize_audio(
    raw: Any,
    audio_bytes: bytes,
    mime_type: str,
    *,
    bot_id: str,
    trust_level: "TrustLevel",
) -> InboundAudio:
    """Build an InboundAudio envelope from a Discord audio message.

    Security: trust is always 'user'. Bot messages are filtered by
    on_message().
    """
    is_thread = isinstance(raw.channel, discord.Thread)
    scope_id = f"thread:{raw.channel.id}" if is_thread else f"channel:{raw.channel.id}"
    user_id = f"dc:user:{raw.author.id}"
    timestamp = raw.created_at
    platform_meta = {
        "guild_id": raw.guild.id if raw.guild else None,
        "channel_id": raw.channel.id,
        "message_id": raw.id,
    }
    routing = RoutingContext(
        platform=Platform.DISCORD.value,
        bot_id=bot_id,
        scope_id=scope_id,
        thread_id=str(raw.channel.id) if is_thread else None,
        reply_to_message_id=str(raw.id),
        platform_meta=dict(platform_meta),
    )
    return InboundAudio(
        id=f"discord:{user_id}:{int(timestamp.timestamp())}:{raw.id}",
        platform=Platform.DISCORD.value,
        bot_id=bot_id,
        scope_id=scope_id,
        user_id=user_id,
        audio_bytes=audio_bytes,
        mime_type=mime_type,
        duration_ms=None,
        file_id=None,
        timestamp=timestamp,
        user_name=(getattr(raw.author, "display_name", None) or raw.author.name),
        is_mention=False,
        trust_level=trust_level,
        platform_meta=platform_meta,
        routing=routing,
    )


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
    # Import here to avoid circular import at module level

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
        raw_ext = msg.mime_type.split("/")[-1] if "/" in msg.mime_type else ""
        ext = raw_ext if raw_ext in _AUDIO_EXTS else "bin"
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
    # Import here to avoid circular import at module level

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
        raw_ext = msg.mime_type.split("/")[-1] if "/" in msg.mime_type else ""
        ext = raw_ext if raw_ext in attachment_exts else "bin"
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
            ref_msg = await messageable.fetch_message(reply_to_id)  # type: ignore[attr-defined]
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

    try:
        assembled = await buffer_audio_chunks(chunks)
    except _PartialAudioError as e:
        await render_audio_fn(e.audio, inbound)
        raise e.cause from e
    if assembled is None:
        return
    await render_audio_fn(assembled, inbound)


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
