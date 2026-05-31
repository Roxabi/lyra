"""Telegram non-audio attachment helpers (T8, #1552).

Extracted from ``telegram_normalize.py`` to keep that module ≤300 lines.

``_make_fetch_closure`` builds the async download closure for a single file_id.
``_extract_attachments`` returns index-aligned (Attachment, PendingAttachment) pairs
for every non-audio media object on a Telegram message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lyra.core.messaging.message import Attachment
from lyra.inbound.attachment_ingest import PendingAttachment

if TYPE_CHECKING:
    from lyra.adapters.telegram import TelegramAdapter


def _make_fetch_closure(adapter: "TelegramAdapter", file_id: str) -> Any:
    """Return an async closure that downloads ``file_id`` bytes via the bot.

    Binds ``file_id`` eagerly (default-arg pattern) to avoid the loop
    late-binding bug when called from a comprehension.
    """

    async def _fetch(
        _adapter: "TelegramAdapter" = adapter,
        _file_id: str = file_id,
    ) -> bytes:
        file_ = await _adapter.bot.get_file(_file_id)
        buf = await _adapter.bot.download_file(file_.file_path)
        if isinstance(buf, (bytes, bytearray)):
            return bytes(buf)
        # BytesIO / file-like
        return buf.read()

    return _fetch


def _extract_attachments(
    adapter: "TelegramAdapter", msg: Any
) -> tuple[list[Attachment], list[PendingAttachment]]:
    """Extract non-audio Attachment + PendingAttachment pairs from a Telegram message.

    Returns two index-aligned lists: (attachments, pending_attachments).
    Each PendingAttachment carries an async fetch closure bound to the adapter
    and the platform file_id, plus declared size metadata for the oversize guard.
    """
    attachments: list[Attachment] = []
    pendings: list[PendingAttachment] = []
    message_id: int | None = getattr(msg, "message_id", None)
    platform_message_id = str(message_id) if message_id is not None else None

    # photo: list of PhotoSize, take largest (last)
    photo = getattr(msg, "photo", None)
    if photo:
        largest = photo[-1]
        file_id: str = largest.file_id
        mime = "image/jpeg"
        attachments.append(
            Attachment(
                type="image",
                url_or_path_or_bytes=f"tg:file_id:{file_id}",
                mime_type=mime,
            )
        )
        pendings.append(
            PendingAttachment(
                fetch=_make_fetch_closure(adapter, file_id),
                mime=mime,
                source="telegram",
                platform_ref=file_id,
                platform_message_id=platform_message_id,
                size=getattr(largest, "file_size", None),
            )
        )

    doc = getattr(msg, "document", None)
    if doc:
        file_id = doc.file_id
        mime = getattr(doc, "mime_type", None) or "application/octet-stream"
        filename = getattr(doc, "file_name", None)
        attachments.append(
            Attachment(
                type="file",
                url_or_path_or_bytes=f"tg:file_id:{file_id}",
                mime_type=mime,
                filename=filename,
            )
        )
        pendings.append(
            PendingAttachment(
                fetch=_make_fetch_closure(adapter, file_id),
                mime=mime,
                source="telegram",
                platform_ref=file_id,
                platform_message_id=platform_message_id,
                filename=filename,
                size=getattr(doc, "file_size", None),
            )
        )

    video = getattr(msg, "video", None)
    if video:
        file_id = video.file_id
        mime = getattr(video, "mime_type", None) or "video/mp4"
        attachments.append(
            Attachment(
                type="video",
                url_or_path_or_bytes=f"tg:file_id:{file_id}",
                mime_type=mime,
            )
        )
        pendings.append(
            PendingAttachment(
                fetch=_make_fetch_closure(adapter, file_id),
                mime=mime,
                source="telegram",
                platform_ref=file_id,
                platform_message_id=platform_message_id,
                size=getattr(video, "file_size", None),
            )
        )

    anim = getattr(msg, "animation", None)
    if anim:
        file_id = anim.file_id
        mime = "image/gif"
        attachments.append(
            Attachment(
                type="image",
                url_or_path_or_bytes=f"tg:file_id:{file_id}",
                mime_type=mime,
            )
        )
        pendings.append(
            PendingAttachment(
                fetch=_make_fetch_closure(adapter, file_id),
                mime=mime,
                source="telegram",
                platform_ref=file_id,
                platform_message_id=platform_message_id,
                size=getattr(anim, "file_size", None),
            )
        )

    sticker = getattr(msg, "sticker", None)
    if sticker:
        # Only static WebP stickers; skip animated (.tgs) and video (.webm)
        if not getattr(sticker, "is_animated", False) and not getattr(
            sticker, "is_video", False
        ):
            file_id = sticker.file_id
            mime = "image/webp"
            attachments.append(
                Attachment(
                    type="image",
                    url_or_path_or_bytes=f"tg:file_id:{file_id}",
                    mime_type=mime,
                )
            )
            pendings.append(
                PendingAttachment(
                    fetch=_make_fetch_closure(adapter, file_id),
                    mime=mime,
                    source="telegram",
                    platform_ref=file_id,
                    platform_message_id=platform_message_id,
                    size=getattr(sticker, "file_size", None),
                )
            )

    return attachments, pendings
