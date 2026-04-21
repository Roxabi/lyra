"""Audio processing for the Telegram adapter."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiogram.types import BufferedInputFile

from lyra.adapters.shared._shared import (
    buffer_and_render_audio,
    mime_to_ext,
    parse_reply_to_id,
    sanitize_filename,
    truncate_caption,
)
from lyra.adapters.telegram.telegram_formatting import (
    _ATTACHMENT_EXTS,
    TELEGRAM_CAPTION_MAX,
    _validate_inbound,
)
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
)

if TYPE_CHECKING:
    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger("lyra.adapters.telegram")


async def _download_audio(
    adapter: TelegramAdapter, file_id: str, duration: int | None = None
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


async def render_audio(
    adapter: TelegramAdapter, msg: OutboundAudio, inbound: InboundMessage
) -> None:
    """Send an OutboundAudio envelope via the appropriate Telegram method.

    Routes based on MIME type:
    - audio/wav, audio/mpeg, audio/mp3 → bot.send_audio() (file player UI)
    - audio/ogg and anything else      → bot.send_voice() (voice bubble UI)
    """
    meta = _validate_inbound(inbound, "render_audio")
    if meta is None:
        return
    chat_id, topic_id, message_id = meta

    # Determine reply target: explicit override first, else original
    reply_to = parse_reply_to_id(msg.reply_to_id)
    if reply_to is None and message_id is not None:
        reply_to = message_id

    duration_sec: int | None = (
        msg.duration_ms // 1000 if msg.duration_ms is not None else None
    )

    audio_buf = BytesIO(msg.audio_bytes)
    use_audio_method = msg.mime_type in ("audio/wav", "audio/mpeg", "audio/mp3")
    audio_buf.name = "audio.wav" if use_audio_method else "voice.ogg"

    kwargs: dict[str, Any] = {"chat_id": chat_id}
    if topic_id is not None:
        kwargs["message_thread_id"] = topic_id
    if reply_to is not None:
        kwargs["reply_to_message_id"] = reply_to
    if msg.caption:
        kwargs["caption"] = msg.caption[:TELEGRAM_CAPTION_MAX]
    if duration_sec is not None:
        kwargs["duration"] = duration_sec

    input_file = BufferedInputFile(audio_buf.read(), filename=audio_buf.name)

    if use_audio_method:
        kwargs["audio"] = input_file
        await adapter.bot.send_audio(**kwargs)
    else:
        kwargs["voice"] = input_file
        await adapter.bot.send_voice(**kwargs)


async def render_attachment(
    adapter: TelegramAdapter, msg: OutboundAttachment, inbound: InboundMessage
) -> None:
    """Send an OutboundAttachment envelope via the appropriate Telegram method.

    Dispatches to send_photo, send_video, or send_document based on msg.type.
    """
    meta = _validate_inbound(inbound, "render_attachment")
    if meta is None:
        return
    chat_id, topic_id, message_id = meta

    reply_to = parse_reply_to_id(msg.reply_to_id)
    if reply_to is None and message_id is not None:
        reply_to = message_id

    buf = BytesIO(msg.data)
    # Derive safe filename: sanitize explicit name or fallback from mime
    if msg.filename:
        buf.name = sanitize_filename(msg.filename, _ATTACHMENT_EXTS)
    else:
        ext = mime_to_ext(msg.mime_type, _ATTACHMENT_EXTS)
        buf.name = f"attachment.{ext}"

    kwargs: dict[str, Any] = {"chat_id": chat_id}
    if topic_id is not None:
        kwargs["message_thread_id"] = topic_id
    if reply_to is not None:
        kwargs["reply_to_message_id"] = reply_to
    truncated = truncate_caption(msg.caption, TELEGRAM_CAPTION_MAX)
    if truncated:
        kwargs["caption"] = truncated

    if msg.type == "image":
        kwargs["photo"] = buf
        await adapter.bot.send_photo(**kwargs)
    elif msg.type == "video":
        kwargs["video"] = buf
        await adapter.bot.send_video(**kwargs)
    else:
        # "document" and "file" both use send_document
        kwargs["document"] = buf
        await adapter.bot.send_document(**kwargs)


async def render_audio_stream(
    adapter: TelegramAdapter,
    chunks: AsyncIterator[OutboundAudioChunk],
    inbound: InboundMessage,
) -> None:
    """Buffer streamed audio chunks and send as a single Telegram voice note."""
    meta = _validate_inbound(inbound, "render_audio_stream")
    if meta is None:
        return
    await buffer_and_render_audio(
        chunks, inbound, lambda audio, msg: render_audio(adapter, audio, msg)
    )


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
