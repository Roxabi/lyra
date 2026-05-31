"""Inbound message handling for the Telegram adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramAPIError

from lyra.adapters.telegram.telegram_audio import _download_audio
from lyra.adapters.telegram.telegram_formatting import _make_send_kwargs
from lyra.adapters.telegram.telegram_normalize import _make_scope_id, normalize_audio
from lyra.core.auth.trust import TrustLevel
from lyra.inbound.attachment_ingest import (
    AttachmentIngestError,
    AttachmentIngestStage,
    PendingAttachment,
)
from lyra.inbound.context import DispatchCtx, InboundContext, RouterCtx, SessionCtx
from lyra.inbound.dispatcher import Dispatcher
from lyra.inbound.pipeline import InboundPipeline
from lyra.inbound.prebuilt_parser import PrebuiltParser
from lyra.inbound.router import Router
from lyra.inbound.session_builder import SessionBuilder
from lyra.inbound.wire_parser_telegram import TelegramWireParser

if TYPE_CHECKING:
    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger("lyra.adapters.telegram")


_dispatcher = Dispatcher()
_router = Router()
_session_builder = SessionBuilder()
_pipeline = InboundPipeline(
    router=_router,
    session_builder=_session_builder,
    dispatcher=_dispatcher,
    ingest_stage=AttachmentIngestStage(),
)
# Adapters are process-singletons created at bootstrap; id-keying is safe for
# this lifecycle (no GC + id-reuse window). Revisit when bootstrap DI lands (#1283).
_parser_cache: dict[int, TelegramWireParser] = {}  # one parser per adapter instance


async def handle_message(adapter: "TelegramAdapter", msg: Any) -> None:
    """Handle an incoming aiogram message: apply backpressure and put on bus."""
    # Defense-in-depth bot filter (TelegramWireParser also filters, but keep fast path).
    if not msg.from_user or getattr(msg.from_user, "is_bot", False):
        return

    # IMPORTANT: Always return normally to aiogram — webhook must return
    # {"ok": True} (HTTP 200). Never raise here or Telegram will retry
    # the update indefinitely.
    adapter._start_typing(msg.chat.id)

    # Per-adapter parser — avoid recreating each message.
    parser = _parser_cache.get(id(adapter))
    if parser is None:
        parser = TelegramWireParser(adapter)
        _parser_cache[id(adapter)] = parser

    inbound_ctx = InboundContext(
        router=RouterCtx(
            bot_id=adapter._bot_id,
            owned_threads=set(),  # Telegram has no thread model; Router only reads
            watch_channels=None,
        ),
        session=SessionCtx(
            turn_store=adapter._turn_store,
            thread_store=None,  # Telegram has no thread model
        ),
        dispatch=DispatchCtx(
            inbound_bus=adapter._inbound_bus,
            circuit_registry=adapter._circuit_registry,
            outbound_listener=adapter._outbound_listener,
            typing=adapter._typing,
            msg_catalog=adapter._msg_manager,
        ),
        ingest=getattr(adapter, "_ingest_ctx", None),
    )

    async def _tg_backpressure(text: str) -> None:
        await adapter.bot.send_message(msg.chat.id, text)

    try:
        await _pipeline.run(
            msg,
            inbound_ctx,
            parser,
            send_backpressure=_tg_backpressure,
            on_drop=lambda: adapter._cancel_typing(msg.chat.id),
        )
    except AttachmentIngestError as e:
        try:
            await adapter.bot.send_message(
                **_make_send_kwargs(msg.chat.id, e.user_message, msg.message_id)
            )
        except TelegramAPIError:
            log.warning(
                "Failed to send attachment-too-large reply for chat_id=%s",
                msg.chat.id,
            )
        return


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

    inbound_ctx = InboundContext(
        router=RouterCtx(
            bot_id=adapter._bot_id,
            owned_threads=set(),  # Telegram has no thread model
            watch_channels=None,
        ),
        session=SessionCtx(
            turn_store=adapter._turn_store,
            thread_store=None,  # Telegram has no thread model
        ),
        dispatch=DispatchCtx(
            inbound_bus=adapter._inbound_bus,
            circuit_registry=adapter._circuit_registry,
            outbound_listener=adapter._outbound_listener,
            typing=adapter._typing,
            msg_catalog=adapter._msg_manager,
        ),
        ingest=getattr(adapter, "_ingest_ctx", None),
    )

    async def _send_bp(text: str) -> None:
        await adapter.bot.send_message(**_make_send_kwargs(chat_id, text, message_id))

    await _pipeline.run(
        hub_audio,
        inbound_ctx,
        PrebuiltParser(),
        send_backpressure=_send_bp,
        on_drop=lambda: adapter._cancel_typing(chat_id),
    )
