"""Inbound message handling for the Telegram adapter."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from lyra.adapters._shared import push_to_hub_guarded
from lyra.adapters.telegram_audio import _download_audio
from lyra.adapters.telegram_formatting import _make_send_kwargs
from lyra.adapters.telegram_normalize import _make_scope_id, normalize_audio
from lyra.core.message import InboundMessage, Platform

if TYPE_CHECKING:
    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger("lyra.adapters.telegram")


async def _push_to_hub(
    adapter: TelegramAdapter,
    hub_msg: InboundMessage,
    on_drop: Callable[[], None] | None = None,
) -> None:
    """Put hub_msg on the inbound bus with circuit-open and backpressure guards.

    on_drop is called before early return in both circuit-open and QueueFull
    cases (e.g. to clean up a temp audio file). Always returns normally so
    aiogram receives HTTP 200.
    """
    chat_id = hub_msg.platform_meta.get("chat_id")

    async def _send_bp(text: str) -> None:
        if chat_id is None:
            log.error(
                "_push_to_hub: platform_meta missing 'chat_id',"
                " dropping backpressure ack for user_id=%s",
                hub_msg.user_id,
            )
            return
        await adapter.bot.send_message(chat_id, text)

    await push_to_hub_guarded(
        inbound_bus=adapter._hub.inbound_bus,
        platform=Platform.TELEGRAM,
        msg=hub_msg,
        circuit_registry=adapter._circuit_registry,
        on_drop=on_drop,
        send_backpressure=_send_bp,
        get_msg=adapter._msg,
    )


async def handle_message(adapter: TelegramAdapter, msg: Any) -> None:
    """Handle an incoming aiogram message: apply backpressure and put on bus."""
    if not msg.from_user or getattr(msg.from_user, "is_bot", False):
        return

    user_id = str(msg.from_user.id)
    identity = adapter._auth.resolve(user_id)
    if adapter._guard_chain.run(identity):
        log.info("auth_reject user=%s channel=telegram", user_id)
        return

    hub_msg = adapter.normalize(
        msg, trust_level=identity.trust_level, is_admin=identity.is_admin
    )

    # In group chats, only respond when directly mentioned.
    # In private chats, always respond.
    if hub_msg.platform_meta.get("is_group") and not hub_msg.is_mention:
        return

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
    await _push_to_hub(
        adapter,
        hub_msg,
        on_drop=lambda: adapter._cancel_typing(msg.chat.id),
    )


async def handle_voice_message(adapter: TelegramAdapter, msg: Any) -> None:
    """Handle an incoming voice or audio message.

    Downloads audio, builds an InboundAudio envelope, and enqueues it
    on the inbound audio bus with backpressure / circuit-open guards.
    """
    if not msg.from_user or getattr(msg.from_user, "is_bot", False):
        return

    uid = str(msg.from_user.id)
    identity = adapter._auth.resolve(uid)
    if adapter._guard_chain.run(identity):
        log.info("auth_reject user=%s channel=telegram", uid)
        return

    voice = msg.voice or msg.audio or getattr(msg, "video_note", None)
    if voice is None:
        return
    file_id = getattr(voice, "file_id", None)
    if file_id is None:
        return

    chat_id: int = msg.chat.id
    message_id: int | None = msg.message_id
    user_id = f"tg:user:{msg.from_user.id}"
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

    # is_admin not propagated to InboundAudio — InboundAudio.is_admin is deferred (see #315)  # noqa: E501
    hub_audio = normalize_audio(
        adapter,
        msg,
        audio_bytes=audio_bytes,
        mime_type="audio/ogg",
        trust_level=identity.trust_level,
    )

    adapter._start_typing(chat_id)
    try:

        async def _send_bp(text: str) -> None:
            await adapter.bot.send_message(
                **_make_send_kwargs(chat_id, text, message_id)
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
        adapter._cancel_typing(chat_id)
