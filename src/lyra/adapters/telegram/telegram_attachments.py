"""Telegram non-audio attachment helpers (T8, #1552).

Extracted from ``telegram_normalize.py`` to keep that module ≤300 lines.

``_make_fetch_closure`` builds the async download closure for a single file_id.
``_extract_attachments`` returns index-aligned (Attachment, PendingAttachment) pairs
for every non-audio media object on a Telegram message.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from lyra.core.messaging.message import Attachment
from lyra.inbound.attachment_ingest import (
    MAX_ATTACHMENT_INGEST_BYTES,
    PendingAttachment,
)

if TYPE_CHECKING:
    from typing import Literal

    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger("lyra.adapters.telegram")


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
    Oversize attachments (size > MAX_ATTACHMENT_INGEST_BYTES) are skipped entirely.
    """
    attachments: list[Attachment] = []
    pendings: list[PendingAttachment] = []
    message_id: int | None = getattr(msg, "message_id", None)
    platform_message_id = str(message_id) if message_id is not None else None

    def _check_and_append(
        file_id: str,
        mime: str,
        size: int | None,
        type_: Literal["image", "file", "video"],
        filename: str | None = None,
    ) -> None:
        if size is not None and size > MAX_ATTACHMENT_INGEST_BYTES:
            log.warning(
                "Oversize Telegram %s skipped: %s > %s",
                type_,
                size,
                MAX_ATTACHMENT_INGEST_BYTES,
            )
            return
        attachments.append(
            Attachment(
                type=type_,
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
                size=size,
                filename=filename,
            )
        )

    photo = getattr(msg, "photo", None)
    if photo:
        largest = photo[-1]
        _check_and_append(
            largest.file_id,
            "image/jpeg",
            getattr(largest, "file_size", None),
            "image",
        )

    doc = getattr(msg, "document", None)
    if doc:
        _check_and_append(
            doc.file_id,
            getattr(doc, "mime_type", None) or "application/octet-stream",
            getattr(doc, "file_size", None),
            "file",
            getattr(doc, "file_name", None),
        )

    video = getattr(msg, "video", None)
    if video:
        _check_and_append(
            video.file_id,
            getattr(video, "mime_type", None) or "video/mp4",
            getattr(video, "file_size", None),
            "video",
        )

    anim = getattr(msg, "animation", None)
    if anim:
        _check_and_append(
            anim.file_id,
            "image/gif",
            getattr(anim, "file_size", None),
            "image",
        )

    sticker = getattr(msg, "sticker", None)
    if sticker and not getattr(sticker, "is_animated", False) and not getattr(
        sticker, "is_video", False
    ):
        _check_and_append(
            sticker.file_id,
            "image/webp",
            getattr(sticker, "file_size", None),
            "image",
        )

    return attachments, pendings
