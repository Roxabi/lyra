"""Inbound message handling for the Telegram adapter."""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramAPIError

from lyra.adapters.shared._shared import push_to_hub_guarded
from lyra.adapters.telegram.telegram_audio import _download_audio
from lyra.adapters.telegram.telegram_formatting import _make_send_kwargs
from lyra.adapters.telegram.telegram_normalize import _make_scope_id, normalize_audio
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage, Platform, TelegramMeta
from lyra.inbound.context import DispatchCtx, RouterCtx
from lyra.inbound.dispatcher import Dispatcher
from lyra.inbound.router import RouteDecision, Router

if TYPE_CHECKING:
    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger("lyra.adapters.telegram")

_dispatcher = Dispatcher()
_router = Router()


async def handle_message(adapter: TelegramAdapter, msg: Any) -> None:  # noqa: C901, PLR0915 — DEBT:wiring-bootstrap-deps
    """Handle an incoming aiogram message: apply backpressure and put on bus."""
    if not msg.from_user or getattr(msg.from_user, "is_bot", False):
        return

    # C3: adapters send raw identity fields; Hub resolves trust in run().
    hub_msg = adapter.normalize(msg, trust_level=TrustLevel.PUBLIC, is_admin=False)

    # Route decision: drop group messages without mention; pass DMs and mentions.
    _router_ctx = RouterCtx(
        bot_id=adapter._bot_id,
        owned_threads=set(),  # Telegram has no thread model
        watch_channels=None,
    )
    if _router.decide(hub_msg, _router_ctx) is RouteDecision.DROP:
        return

    # Session wiring: inject prior session_id + persist callback.
    _new_thread_session_id: str | None = None
    _session_update_fn = None
    if adapter._turn_store is not None:
        from lyra.core.hub.hub_protocol import RoutingKey

        _pool_id = RoutingKey(
            Platform.TELEGRAM, adapter._bot_id, hub_msg.scope_id
        ).to_pool_id()
        try:
            _new_thread_session_id = await adapter._turn_store.get_last_session(
                _pool_id
            )
        except Exception:
            log.exception("TurnStore.get_last_session failed for pool_id=%s", _pool_id)
        _ts = adapter._turn_store

        async def _tg_session_update_fn(
            msg: InboundMessage, session_id: str, pool_id: str
        ) -> None:
            await _ts.start_session(session_id, pool_id)

        _session_update_fn = _tg_session_update_fn

    _replacements: dict[str, Any] = {}
    _is_tg_meta = isinstance(hub_msg.platform_meta, TelegramMeta)
    if _new_thread_session_id is not None and _is_tg_meta:
        _replacements["platform_meta"] = dataclasses.replace(
            hub_msg.platform_meta, thread_session_id=_new_thread_session_id
        )
    if _session_update_fn is not None:
        _replacements["session_update_fn"] = _session_update_fn
    if _replacements:
        hub_msg = dataclasses.replace(hub_msg, **_replacements)

    log.info(
        "message_received",
        extra={
            "platform": "telegram",
            "user_id": hub_msg.user_id,
            "scope_id": hub_msg.scope_id,
            "msg_id": hub_msg.id,
        },
    )
    # IMPORTANT: Always return normally to aiogram — webhook must return
    # {"ok": True} (HTTP 200). Never raise here or Telegram will retry
    # the update indefinitely.
    adapter._start_typing(msg.chat.id)
    _meta = hub_msg.platform_meta
    _chat_id = _meta.chat_id if isinstance(_meta, TelegramMeta) else None

    async def _tg_backpressure(text: str) -> None:
        if _chat_id is None:
            log.error(
                "handle_message: platform_meta missing 'chat_id',"
                " dropping backpressure ack for user_id=%s",
                hub_msg.user_id,
            )
            return
        await adapter.bot.send_message(_chat_id, text)

    dispatch_ctx = DispatchCtx(
        inbound_bus=adapter._inbound_bus,
        circuit_registry=adapter._circuit_registry,
        outbound_listener=adapter._outbound_listener,
        typing=adapter._typing,
        msg_catalog=adapter._msg_manager,
    )
    await _dispatcher.dispatch(
        hub_msg,
        dispatch_ctx,
        _tg_backpressure,
        on_drop=lambda: adapter._cancel_typing(msg.chat.id),
    )


async def handle_voice_message(adapter: TelegramAdapter, msg: Any) -> None:
    """Handle an incoming voice or audio message.

    Downloads audio, builds an InboundMessage (modality='voice') envelope, and
    enqueues it on the inbound bus with backpressure / circuit-open guards.
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

    # C3: trust resolved by Hub; adapter passes PUBLIC as raw identity.
    hub_audio = normalize_audio(
        adapter,
        msg,
        audio_bytes=audio_bytes,
        mime_type="audio/ogg",
        trust_level=TrustLevel.PUBLIC,
    )

    adapter._start_typing(chat_id)
    try:

        async def _send_bp(text: str) -> None:
            await adapter.bot.send_message(
                **_make_send_kwargs(chat_id, text, message_id)
            )

        await push_to_hub_guarded(
            inbound_bus=adapter._inbound_bus,
            platform=Platform.TELEGRAM,
            msg=hub_audio,
            circuit_registry=adapter._circuit_registry,
            on_drop=None,
            send_backpressure=_send_bp,
            get_msg=adapter._msg,
            outbound_listener=adapter._outbound_listener,
        )
    finally:
        adapter._cancel_typing(chat_id)
