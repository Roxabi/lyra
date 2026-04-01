"""Inbound message handling for DiscordAdapter (on_message logic)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters._shared import AUDIO_MIME_TYPES, push_to_hub_guarded
from lyra.adapters.discord_audio import handle_audio as _handle_audio
from lyra.adapters.discord_formatting import make_thread_name
from lyra.adapters.discord_threads import (
    persist_thread_claim,
    persist_thread_session,
    retrieve_thread_session,
)
from lyra.core.message import InboundMessage, Platform
from lyra.core.trust import TrustLevel

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger("lyra.adapters.discord")


async def handle_message(adapter: "DiscordAdapter", message: Any) -> None:  # noqa: C901, PLR0915 — gateway dispatch: each message type branch is independent
    """Handle incoming Gateway message.

    Filters own/bot messages, creates auto-thread before normalization,
    applies backpressure, and enqueues to hub bus.
    """
    # Discard bot messages early — before normalization to avoid waste.
    if message.author.bot:
        return

    # C3: adapters send raw identity fields; Hub resolves trust in run().
    # Audio attachment detection
    audio_attachment = next(
        (
            a
            for a in (getattr(message, "attachments", None) or [])
            if getattr(a, "content_type", "") in AUDIO_MIME_TYPES
        ),
        None,
    )
    if audio_attachment is not None:
        await _handle_audio(adapter, message, audio_attachment, TrustLevel.PUBLIC)
        return  # audio messages handled separately; skip text path

    # Voice command dispatch — guild-only; runs before mention/DM filter.
    if message.guild is not None:
        if await adapter._handle_voice_command(message, TrustLevel.PUBLIC):
            return

    # Pre-detect mention (needed for auto-thread decision)
    _is_mention = (
        adapter._bot_user is not None and adapter._bot_user in message.mentions
    )

    # In DMs (no guild), always respond.
    # In servers: only respond when directly mentioned or in an owned thread.
    _is_dm = message.guild is None
    _is_thread = isinstance(message.channel, discord.Thread)
    _in_owned_thread = _is_thread and message.channel.id in adapter._owned_threads

    # Cold-path lazy check: thread not in hot set, query DB and warm cache on hit.
    if (
        not _is_dm
        and not _is_mention
        and not _in_owned_thread
        and _is_thread
        and adapter._thread_store is not None
    ):
        try:
            if await adapter._thread_store.is_owned(
                str(message.channel.id), adapter._bot_id
            ):
                adapter._owned_threads.add(message.channel.id)
                _in_owned_thread = True
        except Exception:
            log.warning(
                "ThreadStore: lazy is_owned check failed for thread_id=%s",
                message.channel.id,
            )

    # Watch channel: process all messages in designated channels (no mention needed).
    _is_watch_channel = (
        not _is_dm and not _is_thread and message.channel.id in adapter._watch_channels
    )

    _should_process = (
        _is_dm
        or _is_mention
        or _in_owned_thread
        or _is_watch_channel
    )
    if not _should_process:
        return

    # Auto-thread creation BEFORE normalize() (frozen dataclass)
    resolved_thread_id: int | None = None
    resolved_channel_id: int = message.channel.id
    if (
        adapter._auto_thread
        and (_is_mention or _is_watch_channel)
        and not isinstance(message.channel, discord.Thread)
        and hasattr(message.channel, "create_thread")
    ):
        try:
            thread = await message.create_thread(
                name=make_thread_name(message.content, message.author.display_name)
            )
            resolved_thread_id = thread.id
            adapter._owned_threads.add(thread.id)
            if adapter._thread_store is not None:
                asyncio.create_task(
                    persist_thread_claim(
                        adapter._thread_store,
                        thread_id=thread.id,
                        bot_id=adapter._bot_id,
                        channel_id=message.channel.id,
                        guild_id=getattr(message.guild, "id", None),
                    )
                )
        except Exception:
            log.exception(
                "Failed to create Discord thread for message id=%s",
                message.id,
            )
            # Discord may have created the thread despite the error —
            # recover thread_id to keep scope_id consistent.
            if hasattr(message, "thread") and message.thread is not None:
                resolved_thread_id = message.thread.id
                adapter._owned_threads.add(message.thread.id)
                if adapter._thread_store is not None:
                    asyncio.create_task(
                        persist_thread_claim(
                            adapter._thread_store,
                            thread_id=message.thread.id,
                            bot_id=adapter._bot_id,
                            channel_id=message.channel.id,
                            guild_id=getattr(message.guild, "id", None),
                        )
                    )

    # Claim an existing thread when directly mentioned inside it.
    if _is_mention and isinstance(message.channel, discord.Thread):
        adapter._owned_threads.add(message.channel.id)
        if adapter._thread_store is not None:
            asyncio.create_task(
                persist_thread_claim(
                    adapter._thread_store,
                    thread_id=message.channel.id,
                    bot_id=adapter._bot_id,
                    channel_id=getattr(
                        message.channel, "parent_id", message.channel.id
                    ),
                    guild_id=getattr(message.guild, "id", None),
                )
            )

    # Retrieve stored session for existing owned threads (read-side fix).
    # New auto-threads have no prior session; skip get_session() for those.
    _stored_session_id: str | None = None
    _stored_pool_id: str | None = None
    if _in_owned_thread and adapter._thread_store is not None:
        try:
            _stored_session_id, _stored_pool_id = await retrieve_thread_session(
                adapter._thread_store,
                thread_id=str(message.channel.id),
                bot_id=adapter._bot_id,
                cache=adapter._thread_sessions,
            )
        except Exception:
            log.exception(
                "ThreadStore: failed to retrieve session for thread_id=%s",
                message.channel.id,
            )

    try:
        hub_msg = adapter.normalize(
            message,
            thread_id=resolved_thread_id,
            channel_id=resolved_channel_id,
            trust_level=TrustLevel.PUBLIC,
            is_admin=False,
        )
    except Exception:
        log.exception("Failed to normalize discord message id=%s", message.id)
        return

    # Inject stored session + persistence callback into platform_meta.
    _meta_updates: dict[str, Any] = {}
    if _stored_session_id is not None:
        _meta_updates["thread_session_id"] = _stored_session_id
    _has_thread_id = hub_msg.platform_meta.get("thread_id") is not None
    if _has_thread_id and adapter._thread_store is not None:
        _ts = adapter._thread_store
        _bid, _cache = adapter._bot_id, adapter._thread_sessions

        async def _session_update_fn(
            msg: InboundMessage, session_id: str, pool_id: str
        ) -> None:
            await persist_thread_session(_ts, msg, session_id, pool_id, _bid, _cache)

        _meta_updates["_session_update_fn"] = _session_update_fn
    if _meta_updates:
        hub_msg = dataclasses.replace(
            hub_msg,
            platform_meta={**hub_msg.platform_meta, **_meta_updates},
        )

    log.info(
        "message_received",
        extra={
            "platform": "discord",
            "user_id": hub_msg.user_id,
            "scope_id": hub_msg.scope_id,
            "msg_id": hub_msg.id,
        },
    )

    send_to_id: int = (
        resolved_thread_id if resolved_thread_id is not None else resolved_channel_id
    )
    adapter._start_typing(send_to_id)
    await _push_to_hub(
        adapter,
        hub_msg,
        source_message=message,
        on_drop=lambda: adapter._cancel_typing(send_to_id),
    )


async def _push_to_hub(
    adapter: "DiscordAdapter",
    hub_msg: InboundMessage,
    source_message: Any = None,
    on_drop: Callable[[], None] | None = None,
) -> None:
    """Put hub_msg on the inbound bus with circuit-open and backpressure guards.

    on_drop is called before early return in both circuit-open and QueueFull
    cases. Always returns normally.
    """

    async def _send_bp(text: str) -> None:
        if source_message is not None:
            await source_message.reply(text)

    if adapter._outbound_listener is not None:
        adapter._outbound_listener.cache_inbound(hub_msg)

    await push_to_hub_guarded(
        inbound_bus=adapter._inbound_bus,
        platform=Platform.DISCORD,
        msg=hub_msg,
        circuit_registry=adapter._circuit_registry,
        on_drop=on_drop,
        send_backpressure=_send_bp,
        get_msg=adapter._msg,
    )
