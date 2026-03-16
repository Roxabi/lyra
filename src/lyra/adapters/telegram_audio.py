"""Telegram audio processing helpers.

Extracted from telegram.py (issue #297). All functions are free functions
receiving the adapter (or specific fields like bot) as an explicit first argument.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lyra.adapters._shared import (
    _PartialAudioError,
    buffer_audio_chunks,
    parse_reply_to_id,
    push_to_hub_guarded,
)
from lyra.adapters.telegram_formatting import _make_send_kwargs
from lyra.core.auth import TrustLevel
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundAudio,
    OutboundAudioChunk,
    Platform,
    RoutingContext,
)

if TYPE_CHECKING:
    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger(__name__)


def normalize_audio(
    adapter: TelegramAdapter,
    raw: Any,
    audio_bytes: bytes,
    mime_type: str,
    *,
    trust_level: TrustLevel,
) -> InboundAudio:
    """Build an InboundAudio envelope from a Telegram voice/audio/video_note.

    Security: trust is always 'user'. normalize_audio() is never called for
    bot messages. Never logs the bot token.
    """
    if raw.from_user is None:
        raise ValueError(
            "normalize_audio() called with no from_user — "
            "service messages must be filtered before normalization"
        )
    from datetime import timezone

    chat_id: int = raw.chat.id
    topic_id: int | None = getattr(raw, "message_thread_id", None)
    scope_id = adapter._make_scope_id(chat_id, topic_id)
    voice = raw.voice or raw.audio or getattr(raw, "video_note", None)
    duration_ms: int | None = None
    if voice is not None:
        d = getattr(voice, "duration", None)
        if d is not None:
            duration_ms = int(d) * 1000
    file_id: str | None = (
        getattr(voice, "file_id", None) if voice is not None else None
    )
    timestamp = raw.date
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    user_id = f"tg:user:{raw.from_user.id}"
    message_id = getattr(raw, "message_id", None)
    platform_meta = {
        "chat_id": chat_id,
        "topic_id": topic_id,
        "message_id": message_id,
        "is_group": raw.chat.type != "private",
    }
    routing = RoutingContext(
        platform=Platform.TELEGRAM.value,
        bot_id=adapter._bot_id,
        scope_id=scope_id,
        thread_id=str(topic_id) if topic_id is not None else None,
        reply_to_message_id=str(message_id) if message_id is not None else None,
        platform_meta=dict(platform_meta),
    )
    return InboundAudio(
        id=(f"telegram:{user_id}:{int(timestamp.timestamp())}:{file_id or ''}"),
        platform=Platform.TELEGRAM.value,
        bot_id=adapter._bot_id,
        scope_id=scope_id,
        user_id=user_id,
        audio_bytes=audio_bytes,
        mime_type=mime_type,
        duration_ms=duration_ms,
        file_id=file_id,
        timestamp=timestamp,
        user_name=raw.from_user.full_name,
        is_mention=False,
        trust_level=trust_level,
        platform_meta=platform_meta,
        routing=routing,
    )


async def _download_audio(
    adapter: TelegramAdapter,
    file_id: str,
    duration: int | None = None,
) -> tuple[Path, float | None]:
    """Download a Telegram audio/voice file to a local temp file.

    Checks file size against LYRA_MAX_AUDIO_BYTES before downloading.
    Cleans up the temp file if the download fails.

    Returns (path, duration_seconds). Caller is responsible for cleanup.
    """
    file_ = await adapter.bot.get_file(file_id)
    # Pre-download check: skip when file_size is None (Telegram sometimes omits
    # it). The post-download check below always runs and catches that case.
    if file_.file_size is not None and file_.file_size > adapter._max_audio_bytes:
        log.warning(
            "Audio file_id=%s rejected: %d bytes exceeds %d byte limit",
            file_id,
            file_.file_size,
            adapter._max_audio_bytes,
        )
        raise ValueError(
            f"Audio file too large: "
            f"{file_.file_size} > {adapter._max_audio_bytes} bytes"
        )
    _, tmp_str = tempfile.mkstemp(suffix=".ogg", dir=adapter._audio_tmp_dir)
    tmp_path = Path(tmp_str)
    try:
        await adapter.bot.download(file=file_id, destination=tmp_str)
        # Post-download check: always enforced, covers the file_size=None case.
        actual_size = tmp_path.stat().st_size
        if actual_size > adapter._max_audio_bytes:
            tmp_path.unlink(missing_ok=True)
            raise ValueError(
                f"Audio file too large after download: "
                f"{actual_size} > {adapter._max_audio_bytes} bytes"
            )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    log.debug("Downloaded audio file_id=%s to %s", file_id, tmp_path)
    return tmp_path, float(duration) if duration is not None else None


async def _on_voice_message(adapter: TelegramAdapter, msg: Any) -> None:
    """Handle an incoming voice or audio message.

    Downloads audio, builds an InboundAudio envelope, and enqueues it
    on the inbound audio bus with backpressure / circuit-open guards.
    """
    if not msg.from_user or getattr(msg.from_user, "is_bot", False):
        return

    uid = str(msg.from_user.id)
    trust = adapter._auth.check(uid)
    if trust == TrustLevel.BLOCKED:
        log.info("auth_reject user=%s channel=telegram", uid)
        return

    voice = msg.voice or msg.audio or getattr(msg, "video_note", None)
    if voice is None:
        return
    file_id = getattr(voice, "file_id", None)
    if file_id is None:
        return

    user_id = f"tg:user:{msg.from_user.id}"
    scope_id = adapter._make_scope_id(msg.chat.id, msg.message_thread_id)
    log.info(
        "audio_received",
        extra={
            "platform": "telegram",
            "user_id": user_id,
            "scope_id": scope_id,
        },
    )

    try:
        tmp_path, _duration_s = await adapter._download_audio(
            file_id, getattr(voice, "duration", None)
        )
    except ValueError:
        # File too large — notify user, reply to their message (mirrors Discord)
        try:
            _text = adapter._msg(
                "audio_too_large",
                "That audio file is too large to process.",
            )
            await adapter.bot.send_message(
                **_make_send_kwargs(msg.chat.id, _text, msg.message_id)
            )
        except Exception:
            log.warning(
                "Failed to send audio-too-large reply for user_id=%s",
                user_id,
            )
        return
    except Exception:
        log.exception(
            "Failed to download audio file_id=%r for user_id=%s",
            file_id,
            user_id,
        )
        return

    try:
        audio_bytes = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)

    hub_audio = adapter.normalize_audio(
        msg, audio_bytes=audio_bytes, mime_type="audio/ogg", trust_level=trust
    )

    adapter._start_typing(msg.chat.id)
    try:

        async def _send_bp(text: str) -> None:
            await adapter.bot.send_message(
                **_make_send_kwargs(msg.chat.id, text, msg.message_id)
            )

        await push_to_hub_guarded(
            inbound_bus=adapter._hub.inbound_audio_bus,
            platform=Platform.TELEGRAM,
            msg=hub_audio,
            circuit_registry=adapter._circuit_registry,
            on_drop=None,
            send_backpressure=_send_bp,
            get_msg=adapter._msg,
        )
    finally:
        adapter._cancel_typing(msg.chat.id)


async def render_audio(
    bot: Any, msg: OutboundAudio, inbound: InboundMessage
) -> None:
    """Send an OutboundAudio envelope via the appropriate Telegram method.

    Routes based on MIME type:
    - audio/wav, audio/mpeg, audio/mp3 → bot.send_audio() (file player UI)
    - audio/ogg and anything else      → bot.send_voice() (voice bubble UI)

    Uses a BytesIO buffer — no temp file required.
    caption (if set) is attached to the message.
    reply_to_message_id is derived from inbound.platform_meta["message_id"]
    unless msg.reply_to_id overrides it explicitly.
    """
    if inbound.platform != Platform.TELEGRAM.value:
        log.error(
            "render_audio() called with non-telegram message id=%s",
            inbound.id,
        )
        return

    chat_id: int | None = inbound.platform_meta.get("chat_id")
    if chat_id is None:
        log.error(
            "render_audio: platform_meta missing 'chat_id' for msg id=%s",
            inbound.id,
        )
        return

    topic_id: int | None = inbound.platform_meta.get("topic_id")
    message_id: int | None = inbound.platform_meta.get("message_id")

    # Determine reply target: explicit override first, else original
    reply_to = parse_reply_to_id(msg.reply_to_id)
    if reply_to is None and message_id is not None:
        reply_to = message_id

    duration_sec: int | None = (
        msg.duration_ms // 1000 if msg.duration_ms is not None else None
    )

    audio_buf = BytesIO(msg.audio_bytes)

    use_audio_method = msg.mime_type in ("audio/wav", "audio/mpeg", "audio/mp3")

    if use_audio_method:
        audio_buf.name = "audio.wav"
    else:
        audio_buf.name = "voice.ogg"

    kwargs: dict = {"chat_id": chat_id}
    if topic_id is not None:
        kwargs["message_thread_id"] = topic_id
    if reply_to is not None:
        kwargs["reply_to_message_id"] = reply_to
    if msg.caption:
        kwargs["caption"] = msg.caption[:1024]
    if duration_sec is not None:
        kwargs["duration"] = duration_sec

    from aiogram.types import BufferedInputFile

    filename = audio_buf.name
    input_file = BufferedInputFile(audio_buf.read(), filename=filename)

    if use_audio_method:
        kwargs["audio"] = input_file
        await bot.send_audio(**kwargs)
    else:
        kwargs["voice"] = input_file
        await bot.send_voice(**kwargs)


async def render_audio_stream(
    adapter: TelegramAdapter,
    chunks: AsyncIterator[OutboundAudioChunk],
    inbound: InboundMessage,
) -> None:
    """Buffer streamed audio chunks and send as a single Telegram voice note."""
    if inbound.platform != Platform.TELEGRAM.value:
        log.error(
            "render_audio_stream() called with non-telegram message id=%s",
            inbound.id,
        )
        return

    chat_id: int | None = inbound.platform_meta.get("chat_id")
    if chat_id is None:
        log.error(
            "render_audio_stream: platform_meta missing 'chat_id' for msg id=%s",
            inbound.id,
        )
        return

    try:
        assembled = await buffer_audio_chunks(chunks)
    except _PartialAudioError as e:
        await render_audio(adapter.bot, e.audio, inbound)
        raise e.cause from e
    if assembled is None:
        return
    await render_audio(adapter.bot, assembled, inbound)


async def render_voice_stream(
    chunks: AsyncIterator[OutboundAudioChunk],
    inbound: InboundMessage,
) -> None:
    """Not supported on Telegram — drain iterator and log a warning."""
    log.warning(
        "render_voice_stream() is not supported on Telegram (msg id=%s) — "
        "use render_audio_stream() instead; draining iterator",
        inbound.id,
    )
    async for _ in chunks:
        pass
