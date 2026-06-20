"""Inbound message handling for the Telegram adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramAPIError

from factory.adapters.shared.inbound import (
    build_telegram_inbound_ctx,
    get_inbound_pipeline_kit,
    get_or_create_parser,
    run_inbound_guarded,
)
from factory.adapters.telegram.telegram_audio import _download_audio
from factory.adapters.telegram.telegram_formatting import _make_send_kwargs
from factory.adapters.telegram.telegram_normalize import _make_scope_id, normalize_audio
from factory.core.auth.trust import TrustLevel
from factory.inbound.attachment_ingest import AttachmentIngestError, PendingAttachment
from factory.inbound.prebuilt_parser import PrebuiltParser
from factory.inbound.wire_parser_telegram import TelegramWireParser

if TYPE_CHECKING:
    from factory.adapters.telegram import TelegramAdapter

log = logging.getLogger("factory.adapters.telegram")


def _expected_media_count(msg: Any) -> int:
    """Count media items on the raw aiogram message that would be extracted."""
    expected = 0
    if getattr(msg, "photo", None):
        expected += 1
    if getattr(msg, "document", None):
        expected += 1
    if getattr(msg, "video", None):
        expected += 1
    if getattr(msg, "animation", None):
        expected += 1
    sticker = getattr(msg, "sticker", None)
    if (
        sticker
        and not getattr(sticker, "is_animated", False)
        and not getattr(sticker, "is_video", False)
    ):
        expected += 1
    return expected


async def _send_oversize_reply(
    adapter: "TelegramAdapter", msg: Any, parsed: Any
) -> None:
    """Notify the user when oversize attachments were filtered (T6, #1561)."""
    expected = _expected_media_count(msg)
    if len(parsed.attachments) < expected:
        try:
            _text = adapter._msg(
                "attachment_too_large",
                "That file is too large to process.",
            )
            await adapter.bot.send_message(
                **_make_send_kwargs(msg.chat.id, _text, msg.message_id)
            )
        except TelegramAPIError:
            log.warning(
                "Failed to send attachment-too-large reply for chat_id=%s",
                msg.chat.id,
            )


async def handle_message(adapter: "TelegramAdapter", msg: Any) -> None:
    """Handle an incoming aiogram message: apply backpressure and put on bus."""
    # Defense-in-depth bot filter (TelegramWireParser also filters, but keep fast path).
    if not msg.from_user or getattr(msg.from_user, "is_bot", False):
        return

    # IMPORTANT: Always return normally to aiogram — webhook must return
    # {"ok": True} (HTTP 200). Never raise here or Telegram will retry
    # the update indefinitely.
    adapter._start_typing(msg.chat.id)

    kit = get_inbound_pipeline_kit()
    parser = get_or_create_parser(kit.parser_cache, adapter, TelegramWireParser)
    inbound_ctx = build_telegram_inbound_ctx(adapter, ingest=adapter._ingest_ctx)

    # Pre-parse to detect oversize-attachment filtering (T6/T7, #1561).
    parsed = parser.parse(msg, inbound_ctx)
    if parsed is None:
        return

    await _send_oversize_reply(adapter, msg, parsed)

    # T7: Drop if no attachments survived and no text.
    if not parsed.attachments and not parsed.text:
        adapter._cancel_typing(msg.chat.id)
        return

    async def _tg_backpressure(text: str) -> None:
        await adapter.bot.send_message(msg.chat.id, text)

    async def _on_ingest_error(exc: AttachmentIngestError) -> None:
        try:
            await adapter.bot.send_message(
                **_make_send_kwargs(msg.chat.id, exc.user_message, msg.message_id)
            )
        except TelegramAPIError:
            log.warning(
                "Failed to send attachment-too-large reply for chat_id=%s",
                msg.chat.id,
            )

    await run_inbound_guarded(
        pipeline=kit.pipeline,
        raw_message=msg,
        inbound_ctx=inbound_ctx,
        parser=parser,
        log_context=f"telegram chat_id={msg.chat.id}",
        on_attachment_ingest_error=_on_ingest_error,
        send_backpressure=_tg_backpressure,
        on_drop=lambda: adapter._cancel_typing(msg.chat.id),
    )


async def handle_voice_message(adapter: "TelegramAdapter", msg: Any) -> None:  # noqa: C901
    """Handle an incoming voice or audio message.

    Downloads audio eagerly (with early-return error handling), then wraps the
    already-fetched bytes in a trivial FetchFn closure passed to PendingAttachment.
    Routes the voice InboundMessage through InboundPipeline so AttachmentIngestStage
    can call store.put() and stamp a real BlobRef (when a store is configured).
    In no-store mode the stage is a no-op and the PENDING BlobRef reaches the hub
    unchanged — same behavior as before this refactor.
    """
    if not msg.from_user or getattr(msg.from_user, "is_bot", False):
        return

    # C3: adapters send raw identity fields; Hub resolves trust in run().
    voice = msg.voice or msg.audio or getattr(msg, "video_note", None)
    if voice is None:
        return
    file_id = getattr(voice, "file_id", None)
    if file_id is None:
        return

    chat_id: int = msg.chat.id
    message_id: int | None = msg.message_id
    user_id = f"tg:user:{msg.from_user.id}"
    # scope_id is computed here for early logging; normalize_audio() recomputes
    # it independently with the same arguments (both call _make_scope_id).
    scope_id = _make_scope_id(chat_id, msg.message_thread_id)
    log.info(
        "audio_received",
        extra={
            "platform": "telegram",
            "user_id": user_id,
            "scope_id": scope_id,
        },
    )

    # --- Eager download with early-return error handling (preserved from original) ---
    try:
        tmp_path, _duration_s = await _download_audio(
            adapter, file_id, getattr(voice, "duration", None)
        )
    except ValueError:
        # File too large — notify user, reply to their message (mirrors Discord)
        try:
            _text = adapter._msg(
                "audio_too_large",
                "That audio file is too large to process.",
            )
            await adapter.bot.send_message(
                **_make_send_kwargs(chat_id, _text, message_id)
            )
        except TelegramAPIError:
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
        try:
            _text = adapter._msg(
                "audio_download_failed",
                "Couldn't retrieve your audio file. Please try again.",
            )
            await adapter.bot.send_message(
                **_make_send_kwargs(chat_id, _text, message_id)
            )
        except TelegramAPIError:
            log.warning(
                "Failed to send audio-download-failed reply"
                " for user_id=%s message_id=%s",
                user_id,
                message_id,
            )
        return

    try:
        audio_bytes = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)

    # Trivial FetchFn: bytes already in hand; closure satisfies the PendingAttachment
    # contract so AttachmentIngestStage can call store.put() when a store is present.
    async def _tg_voice_fetch() -> bytes:
        return audio_bytes

    pending = PendingAttachment(
        fetch=_tg_voice_fetch,
        mime="audio/ogg",
        source="telegram",
        platform_ref=file_id,
        platform_message_id=str(message_id) if message_id is not None else None,
    )

    # C3: trust resolved by Hub; adapter passes PUBLIC as raw identity.
    hub_audio = normalize_audio(
        adapter,
        msg,
        audio_bytes,
        "audio/ogg",
        trust_level=TrustLevel.PUBLIC,
        pending=pending,
    )

    adapter._start_typing(chat_id)

    inbound_ctx = build_telegram_inbound_ctx(adapter, ingest=adapter._ingest_ctx)
    kit = get_inbound_pipeline_kit()

    async def _send_bp(text: str) -> None:
        await adapter.bot.send_message(**_make_send_kwargs(chat_id, text, message_id))

    await run_inbound_guarded(
        pipeline=kit.pipeline,
        raw_message=hub_audio,
        inbound_ctx=inbound_ctx,
        parser=PrebuiltParser(),
        log_context=f"telegram voice chat_id={chat_id} user_id={user_id}",
        on_attachment_ingest_error=_noop_ingest_error,
        send_backpressure=_send_bp,
        on_drop=lambda: adapter._cancel_typing(chat_id),
    )


async def _noop_ingest_error(_exc: AttachmentIngestError) -> None:
    """Voice path uses pre-built hub_audio — ingest errors are not expected."""
