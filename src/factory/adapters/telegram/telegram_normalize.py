"""Inbound message normalization for the Telegram adapter."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timezone
from typing import TYPE_CHECKING, Any

from factory.adapters.telegram.telegram_attachments import _extract_attachments
from factory.core.audio_payload import AudioPayload
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import (
    InboundMessage,
    Platform,
    RoutingContext,
    TelegramMeta,
)
from factory.inbound.attachment_ingest import PendingAttachment

if TYPE_CHECKING:
    from factory.adapters.telegram import TelegramAdapter

log = logging.getLogger("factory.adapters.telegram")


def _make_scope_id(
    chat_id: int,
    topic_id: int | None,
) -> str:
    """Build the canonical scope_id for a Telegram chat/topic."""
    if topic_id is not None:
        return f"chat:{chat_id}:topic:{topic_id}"
    return f"chat:{chat_id}"


@dataclass(frozen=True)
class RoutingDeps:
    """Frozen deps for _build_routing — groups related metadata fields."""

    adapter: "TelegramAdapter"
    chat_id: int
    topic_id: int | None
    message_id: int | None
    scope_id: str
    is_group: bool


def _build_routing(deps: RoutingDeps) -> tuple[TelegramMeta, RoutingContext]:
    """Build TelegramMeta and RoutingContext for a Telegram message."""
    platform_meta = TelegramMeta(
        chat_id=deps.chat_id,
        topic_id=deps.topic_id,
        message_id=deps.message_id,
        is_group=deps.is_group,
    )
    routing = RoutingContext(
        platform=Platform.TELEGRAM.value,
        bot_id=deps.adapter._bot_id,
        scope_id=deps.scope_id,
        thread_id=str(deps.topic_id) if deps.topic_id is not None else None,
        reply_to_message_id=(
            str(deps.message_id) if deps.message_id is not None else None
        ),
        platform_meta=platform_meta,
    )
    return platform_meta, routing


def normalize(  # noqa: C901 — DEBT:wiring-bootstrap-deps
    adapter: TelegramAdapter,
    raw: Any,
    *,
    trust_level: TrustLevel = TrustLevel.TRUSTED,
    is_admin: bool = False,  # REQUIRED: always pass is_admin=identity.is_admin
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

    bot_suffix = f"@{adapter._bot_username}" if adapter._bot_username else None

    # is_mention is always False in private chats
    is_mention = False
    if is_group and raw.entities and bot_suffix is not None:
        for entity in raw.entities:
            if entity.type == "mention":
                slice_text = raw.text[entity.offset : entity.offset + entity.length]
                if slice_text == bot_suffix:
                    is_mention = True
                    break
            elif entity.type == "bot_command":
                # /cmd → bare (no suffix, respond); /cmd@botname → only if our bot
                cmd_text = raw.text[entity.offset : entity.offset + entity.length]
                if "@" not in cmd_text or cmd_text.endswith(bot_suffix):
                    is_mention = True
                    break

    chat_id: int = raw.chat.id
    topic_id: int | None = raw.message_thread_id
    user_id = f"tg:user:{raw.from_user.id}"
    scope_id = _make_scope_id(chat_id, topic_id)

    text = raw.text or getattr(raw, "caption", None) or ""
    # Strip @botname suffix from bot_command entities (/clear@botname → /clear).
    # Telegram group commands include the suffix; CommandRouter expects plain names.
    if raw.entities and bot_suffix is not None:
        for entity in raw.entities:
            if entity.type == "bot_command":
                cmd_text = text[entity.offset : entity.offset + entity.length]
                if cmd_text.endswith(bot_suffix):
                    text = (
                        text[: entity.offset]
                        + cmd_text[: -len(bot_suffix)]
                        + text[entity.offset + entity.length :]
                    )
                break  # only one bot_command entity per message
    # Strip @mention prefix so content reaches the agent clean (align with Discord)
    if is_mention and bot_suffix is not None:
        text = text.replace(bot_suffix, "").strip()
    timestamp = raw.date
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    log.debug(
        "Normalizing message from user_id=%s in chat_id=%s",
        user_id,
        chat_id,
    )

    attachments, pendings = _extract_attachments(adapter, raw)
    message_id = getattr(raw, "message_id", None)
    reply_to_message = getattr(raw, "reply_to_message", None)
    reply_to_id = (
        str(reply_to_message.message_id) if reply_to_message is not None else None
    )
    platform_meta, routing = _build_routing(
        RoutingDeps(
            adapter=adapter,
            chat_id=chat_id,
            topic_id=topic_id,
            message_id=message_id,
            scope_id=scope_id,
            is_group=is_group,
        )
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
        pending_attachments=pendings,
        timestamp=timestamp,
        trust_level=trust_level,
        is_admin=is_admin,
        platform_meta=platform_meta,
        routing=routing,
        reply_to_id=reply_to_id,
    )


def normalize_audio(  # noqa: PLR0913 — ChannelAdapter protocol; pending is additive kwarg
    adapter: "TelegramAdapter",
    raw: Any,
    audio_bytes: bytes,
    mime_type: str,
    *,
    trust_level: TrustLevel,
    pending: PendingAttachment | None = None,
) -> InboundMessage:
    """Build a voice InboundMessage from a Telegram audio/voice/video_note update.

    ``audio_bytes`` and ``mime_type`` are required positional args (ChannelAdapter
    protocol).  ``pending`` is optional: when provided (voice path via
    handle_voice_message), it is stored in ``InboundMessage.pending_attachment`` so
    ``AttachmentIngestStage`` can later persist the bytes and stamp a real BlobRef.
    When absent (existing callers / test helpers), ``pending_attachment`` is None.

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
    is_group = raw.chat.type != "private"
    user_id = f"tg:user:{raw.from_user.id}"
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
    message_id = getattr(raw, "message_id", None)
    reply_to_message = getattr(raw, "reply_to_message", None)
    reply_to_id = (
        str(reply_to_message.message_id) if reply_to_message is not None else None
    )
    platform_meta, routing = _build_routing(
        RoutingDeps(
            adapter=adapter,
            chat_id=chat_id,
            topic_id=topic_id,
            message_id=message_id,
            scope_id=scope_id,
            is_group=is_group,
        )
    )
    return InboundMessage(
        id=(f"telegram:{user_id}:{int(timestamp.timestamp())}:{file_id or ''}"),
        platform=Platform.TELEGRAM.value,
        bot_id=adapter._bot_id,
        scope_id=scope_id,
        user_id=user_id,
        user_name=raw.from_user.full_name,
        is_mention=False,
        text="",
        text_raw="",
        trust_level=trust_level,
        timestamp=timestamp,
        platform_meta=platform_meta,
        routing=routing,
        reply_to_id=reply_to_id,
        modality="voice",
        audio=AudioPayload(
            blob_ref=None,  # stamped by AttachmentIngestStage; None = unresolved
            mime_type=mime_type,
            duration_ms=duration_ms,
            file_id=file_id,
            waveform_b64=None,
        ),
        pending_attachment=pending,
    )
