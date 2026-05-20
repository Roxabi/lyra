"""Inbound message handling for DiscordAdapter (on_message logic)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters.discord.discord_audio import handle_audio as _handle_audio
from lyra.adapters.discord.discord_formatting import make_thread_name
from lyra.adapters.discord.discord_threads import persist_thread_claim
from lyra.adapters.shared._shared import AUDIO_MIME_TYPES
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import DiscordMeta, InboundMessage
from lyra.inbound.context import DispatchCtx, RouterCtx, SessionCtx
from lyra.inbound.dispatcher import Dispatcher
from lyra.inbound.router import RouteDecision, Router
from lyra.inbound.session_builder import SessionBuilder

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger("lyra.adapters.discord")

_dispatcher = Dispatcher()
_router = Router()
_session_builder = SessionBuilder()


async def _discord_pre_route_hook(
    msg: InboundMessage, router_ctx: RouterCtx, adapter: "DiscordAdapter"
) -> None:
    """Cold-path: lazy ThreadStore.is_owned warmup so revived threads route correctly.

    Mutates ``router_ctx.owned_threads`` in place when the DB confirms ownership.
    Called inline in ``handle_message`` before ``Router.decide``.
    """
    meta = msg.platform_meta
    if not isinstance(meta, DiscordMeta):
        return
    if meta.thread_id is None:
        return  # not a thread
    if meta.thread_id in router_ctx.owned_threads:
        return  # hot set already knows
    if adapter._thread_store is None:
        return
    try:
        if await adapter._thread_store.is_owned(str(meta.thread_id), adapter._bot_id):
            router_ctx.owned_threads.add(meta.thread_id)
    except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
        # ThreadStore I/O failure — fall through; Router will DROP unrecognized thread
        log.warning(
            "pre_route_hook: ThreadStore.is_owned failed for thread %s",
            meta.thread_id,
            exc_info=True,
        )


async def handle_message(adapter: "DiscordAdapter", message: Any) -> None:  # noqa: C901, PLR0915 — DEBT:wiring-bootstrap-deps
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

    # Pre-detect mention (needed for auto-thread decision below)
    _is_mention = (
        adapter._bot_user is not None and adapter._bot_user in message.mentions
    )
    _is_dm = message.guild is None
    _is_thread = isinstance(message.channel, discord.Thread)

    # Build routing context; owned_threads shared by reference (pre_route_hook mutates).
    _router_ctx = RouterCtx(
        bot_id=adapter._bot_id,
        owned_threads=adapter._owned_threads,  # mutable — shared by reference
        watch_channels=adapter._watch_channels if adapter._watch_channels else None,
    )
    # Derived routing flag (still needed for auto-thread decision below).
    _is_watch_channel = (
        not _is_dm and not _is_thread and message.channel.id in adapter._watch_channels
    )

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
                await persist_thread_claim(
                    adapter._thread_store,
                    thread_id=thread.id,
                    bot_id=adapter._bot_id,
                    channel_id=message.channel.id,
                    guild_id=getattr(message.guild, "id", None),
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
                    try:
                        await persist_thread_claim(
                            adapter._thread_store,
                            thread_id=message.thread.id,
                            bot_id=adapter._bot_id,
                            channel_id=message.channel.id,
                            guild_id=getattr(message.guild, "id", None),
                        )
                    except Exception as e:  # noqa: BLE001 — DEBT:boundary-broad-catch
                        log.warning(
                            "Failed to persist thread claim in recovery path: %s", e
                        )

    # Claim an existing thread when directly mentioned inside it.
    if _is_mention and isinstance(message.channel, discord.Thread):
        adapter._owned_threads.add(message.channel.id)
        if adapter._thread_store is not None:
            await persist_thread_claim(
                adapter._thread_store,
                thread_id=message.channel.id,
                bot_id=adapter._bot_id,
                channel_id=getattr(message.channel, "parent_id", message.channel.id),
                guild_id=getattr(message.guild, "id", None),
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

    # pre_route_hook: cold-path lazy owned-thread warmup (T20).
    await _discord_pre_route_hook(hub_msg, _router_ctx, adapter)

    # Routing decision — Router reads hub_msg.platform_meta + router_ctx.
    if _router.decide(hub_msg, _router_ctx) is RouteDecision.DROP:
        return

    session_ctx = SessionCtx(
        turn_store=adapter._turn_store,
        thread_store=adapter._thread_store,
        thread_sessions_cache=adapter._thread_sessions,  # MUTABLE — shared by reference
    )
    hub_msg = await _session_builder.build(hub_msg, session_ctx)

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

    async def _dc_backpressure(text: str) -> None:
        await message.reply(text)

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
        _dc_backpressure,
        on_drop=lambda: adapter._cancel_typing(send_to_id),
    )
