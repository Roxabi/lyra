"""Audio detection, normalization, and inbound handling for DiscordAdapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters._shared import push_to_hub_guarded
from lyra.core.audio_payload import AudioPayload
from lyra.core.message import (
    InboundMessage,
    Platform,
    RoutingContext,
)
from lyra.core.scope import user_scoped

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.trust import TrustLevel

log = logging.getLogger(__name__)


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
    # RIFF/WAV — check sub-type to reject non-audio RIFF containers (WebP, AVI)
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WAVE":  # noqa: PLR2004
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
) -> InboundMessage:
    """Build an InboundMessage (modality='voice') envelope from a Discord audio message.

    Security: trust is always 'user'. Bot messages are filtered by
    on_message().
    """
    is_thread = isinstance(raw.channel, discord.Thread)
    scope_id = f"thread:{raw.channel.id}" if is_thread else f"channel:{raw.channel.id}"
    user_id = f"dc:user:{raw.author.id}"
    # User-scope guild channels for audio too (#356).
    is_guild_channel = raw.guild is not None and not is_thread
    if is_guild_channel:
        scope_id = user_scoped(scope_id, user_id)
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
    return InboundMessage(
        id=f"discord:{user_id}:{int(timestamp.timestamp())}:{raw.id}",
        platform=Platform.DISCORD.value,
        bot_id=bot_id,
        scope_id=scope_id,
        user_id=user_id,
        user_name=(getattr(raw.author, "display_name", None) or raw.author.name),
        is_mention=False,
        text="",
        text_raw="",
        trust_level=trust_level,
        timestamp=timestamp,
        platform_meta=platform_meta,
        routing=routing,
        modality="voice",
        audio=AudioPayload(
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            duration_ms=None,
            file_id=None,
        ),
    )


async def handle_audio(  # noqa: C901 — audio gate mirrors text gate with independent branches
    adapter: "DiscordAdapter",
    message: Any,
    audio_attachment: Any,
    trust: "TrustLevel",
) -> None:
    """Handle an inbound audio attachment."""
    user_id = f"dc:user:{message.author.id}"
    log.info(
        "audio_received",
        extra={
            "platform": "discord",
            "user_id": user_id,
            "message_id": message.id,
        },
    )
    # Pre-download size check (matches Telegram's _download_audio guard)
    att_size = getattr(audio_attachment, "size", None)
    if att_size is None or att_size > adapter._max_audio_bytes:
        log.warning(
            "Audio attachment rejected: %d bytes exceeds %d byte limit (message_id=%s)",
            att_size,
            adapter._max_audio_bytes,
            message.id,
        )
        try:
            await message.reply(
                adapter._msg(
                    "audio_too_large",
                    "That audio file is too large to process.",
                )
            )
        except Exception:
            log.warning(
                "Failed to send audio-too-large reply for message_id=%s",
                message.id,
            )
        return

    try:
        audio_bytes = await audio_attachment.read()
    except Exception:
        log.exception(
            "Failed to download audio attachment for message_id=%s",
            message.id,
        )
        return

    # Magic-byte check: client-supplied content_type is untrusted.
    if not is_valid_audio_magic(audio_bytes):
        log.warning(
            "Audio attachment rejected: magic bytes do not match any known"
            " audio format (message_id=%s)",
            message.id,
        )
        try:
            await message.reply(
                adapter._msg(
                    "audio_invalid_format",
                    "That file does not appear to be a valid audio file.",
                )
            )
        except Exception:
            log.warning(
                "Failed to send invalid-format reply for message_id=%s",
                message.id,
            )
        return

    # Gate: only process audio in DMs, direct mentions, or owned threads.
    _audio_is_dm = message.guild is None
    _audio_is_thread = isinstance(message.channel, discord.Thread)
    _audio_in_owned_thread = (
        _audio_is_thread and message.channel.id in adapter._owned_threads
    )
    _audio_is_mention = (
        adapter._bot_user is not None and adapter._bot_user in message.mentions
    )
    # Cold-path lazy check (same as text path).
    if (
        not _audio_is_dm
        and not _audio_is_mention
        and not _audio_in_owned_thread
        and _audio_is_thread
        and adapter._thread_store is not None
    ):
        try:
            if await adapter._thread_store.is_owned(
                str(message.channel.id), adapter._bot_id
            ):
                adapter._owned_threads.add(message.channel.id)
                _audio_in_owned_thread = True
        except Exception:
            log.warning(
                "ThreadStore: lazy is_owned (audio) failed for thread_id=%s",
                message.channel.id,
            )
    if not _audio_is_dm and not _audio_is_mention and not _audio_in_owned_thread:  # noqa: E501
        return

    hub_audio = adapter.normalize_audio(
        message,
        audio_bytes=audio_bytes,
        mime_type=getattr(audio_attachment, "content_type", "audio/ogg"),
        trust_level=trust,
    )

    async def _send_bp(text: str) -> None:
        await message.reply(text)

    adapter._start_typing(message.channel.id)
    await push_to_hub_guarded(
        inbound_bus=adapter._inbound_bus,
        platform=Platform.DISCORD,
        msg=hub_audio,
        circuit_registry=adapter._circuit_registry,
        on_drop=lambda: adapter._cancel_typing(message.channel.id),
        send_backpressure=_send_bp,
        get_msg=adapter._msg,
        outbound_listener=adapter._outbound_listener,
    )
