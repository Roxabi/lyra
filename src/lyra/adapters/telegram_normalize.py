"""Inbound message normalization for the Telegram adapter."""

from __future__ import annotations

import logging
from datetime import timezone
from typing import TYPE_CHECKING, Any

from lyra.core.message import (
    Attachment,
    InboundAudio,
    InboundMessage,
    Platform,
    RoutingContext,
)
from lyra.core.trust import TrustLevel

if TYPE_CHECKING:
    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger("lyra.adapters.telegram")


def _extract_attachments(msg: Any) -> list[Attachment]:
    """Extract non-audio Attachment objects from a Telegram message."""
    result: list[Attachment] = []
    # photo: list of PhotoSize, take largest (last)
    photo = getattr(msg, "photo", None)
    if photo:
        largest = photo[-1]
        result.append(
            Attachment(
                type="image",
                url_or_path_or_bytes=f"tg:file_id:{largest.file_id}",
                mime_type="image/jpeg",
            )
        )
    doc = getattr(msg, "document", None)
    if doc:
        result.append(
            Attachment(
                type="file",
                url_or_path_or_bytes=f"tg:file_id:{doc.file_id}",
                mime_type=getattr(doc, "mime_type", None) or "application/octet-stream",
                filename=getattr(doc, "file_name", None),
            )
        )
    video = getattr(msg, "video", None)
    if video:
        result.append(
            Attachment(
                type="video",
                url_or_path_or_bytes=f"tg:file_id:{video.file_id}",
                mime_type=getattr(video, "mime_type", None) or "video/mp4",
            )
        )
    anim = getattr(msg, "animation", None)
    if anim:
        result.append(
            Attachment(
                type="image",
                url_or_path_or_bytes=f"tg:file_id:{anim.file_id}",
                mime_type="image/gif",
            )
        )
    sticker = getattr(msg, "sticker", None)
    if sticker:
        # Only static WebP stickers; skip animated (.tgs) and video (.webm)
        if not getattr(sticker, "is_animated", False) and not getattr(
            sticker, "is_video", False
        ):
            result.append(
                Attachment(
                    type="image",
                    url_or_path_or_bytes=f"tg:file_id:{sticker.file_id}",
                    mime_type="image/webp",
                )
            )
    return result


def _make_scope_id(chat_id: int, topic_id: int | None) -> str:
    """Build the canonical scope_id for a Telegram chat/topic."""
    if topic_id is not None:
        return f"chat:{chat_id}:topic:{topic_id}"
    return f"chat:{chat_id}"


def _build_routing(  # noqa: PLR0913 — groups related metadata fields
    adapter: TelegramAdapter,
    chat_id: int,
    topic_id: int | None,
    message_id: int | None,
    scope_id: str,
    is_group: bool,
) -> tuple[dict[str, Any], RoutingContext]:
    """Build platform_meta dict and RoutingContext for a Telegram message."""
    platform_meta: dict[str, Any] = {
        "chat_id": chat_id,
        "topic_id": topic_id,
        "message_id": message_id,
        "is_group": is_group,
    }
    routing = RoutingContext(
        platform=Platform.TELEGRAM.value,
        bot_id=adapter._bot_id,
        scope_id=scope_id,
        thread_id=str(topic_id) if topic_id is not None else None,
        reply_to_message_id=str(message_id) if message_id is not None else None,
        platform_meta=dict(platform_meta),
    )
    return platform_meta, routing


def normalize(
    adapter: TelegramAdapter,
    raw: Any,
    *,
    trust_level: TrustLevel = TrustLevel.TRUSTED,
    is_admin: bool = False,  # REQUIRED: always pass is_admin=identity.is_admin — do not rely on default  # noqa: E501
) -> InboundMessage:
    """Convert an aiogram Message (or SimpleNamespace) to an InboundMessage.

    Security: trust is always 'user'. normalize() is never called for bot
    messages.  Never logs the bot token.
    """
    if raw.from_user is None:
        raise ValueError(
            "normalize() called with no from_user — "
            "service messages must be filtered before normalization"
        )
    is_group = raw.chat.type != "private"

    # is_mention is always False in private chats
    is_mention = False
    if is_group and raw.entities:
        for entity in raw.entities:
            if entity.type == "mention":
                slice_text = raw.text[entity.offset : entity.offset + entity.length]
                if slice_text == f"@{adapter._bot_username}":
                    is_mention = True
                    break

    chat_id: int = raw.chat.id
    topic_id: int | None = raw.message_thread_id
    scope_id = _make_scope_id(chat_id, topic_id)

    text = raw.text or getattr(raw, "caption", None) or ""
    timestamp = raw.date
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    user_id = f"tg:user:{raw.from_user.id}"

    log.debug(
        "Normalizing message from user_id=%s in chat_id=%s",
        user_id,
        chat_id,
    )

    attachments = _extract_attachments(raw)
    message_id = getattr(raw, "message_id", None)
    reply_to_message = getattr(raw, "reply_to_message", None)
    reply_to_id = (
        str(reply_to_message.message_id) if reply_to_message is not None else None
    )
    platform_meta, routing = _build_routing(
        adapter, chat_id, topic_id, message_id, scope_id, is_group
    )
    return InboundMessage(
        id=(f"telegram:{user_id}:{int(timestamp.timestamp())}:{message_id or ''}"),
        platform=Platform.TELEGRAM.value,
        bot_id=adapter._bot_id,
        scope_id=scope_id,
        user_id=user_id,
        user_name=raw.from_user.full_name,
        is_mention=is_mention,
        text=text,
        text_raw=text,
        attachments=attachments,
        timestamp=timestamp,
        trust="user",
        trust_level=trust_level,
        is_admin=is_admin,
        platform_meta=platform_meta,
        routing=routing,
        reply_to_id=reply_to_id,
    )


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
    chat_id: int = raw.chat.id
    topic_id: int | None = getattr(raw, "message_thread_id", None)
    scope_id = _make_scope_id(chat_id, topic_id)
    voice = raw.voice or raw.audio or getattr(raw, "video_note", None)
    duration_ms: int | None = None
    if voice is not None:
        d = getattr(voice, "duration", None)
        if d is not None:
            duration_ms = int(d) * 1000
    file_id: str | None = getattr(voice, "file_id", None) if voice is not None else None
    timestamp = raw.date
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    user_id = f"tg:user:{raw.from_user.id}"
    message_id = getattr(raw, "message_id", None)
    is_group = raw.chat.type != "private"
    platform_meta, routing = _build_routing(
        adapter, chat_id, topic_id, message_id, scope_id, is_group
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
