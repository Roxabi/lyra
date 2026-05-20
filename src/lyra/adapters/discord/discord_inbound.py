"""Inbound message handling for DiscordAdapter (on_message logic)."""

from __future__ import annotations

import dataclasses
import functools
import logging
import sqlite3
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters.discord.discord_audio import handle_audio as _handle_audio
from lyra.adapters.discord.discord_formatting import make_thread_name
from lyra.adapters.discord.discord_threads import persist_thread_claim
from lyra.adapters.shared._shared import AUDIO_MIME_TYPES
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import DiscordMeta, InboundMessage
from lyra.inbound.context import DispatchCtx, InboundContext, RouterCtx, SessionCtx
from lyra.inbound.dispatcher import Dispatcher
from lyra.inbound.pipeline import InboundPipeline
from lyra.inbound.router import Router
from lyra.inbound.session_builder import SessionBuilder
from lyra.inbound.wire_parser_discord import DiscordWireParser

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger("lyra.adapters.discord")

_dispatcher = Dispatcher()
_router = Router()
_session_builder = SessionBuilder()
_pipeline = InboundPipeline(
    router=_router, session_builder=_session_builder, dispatcher=_dispatcher
)
_parser_cache: dict[int, DiscordWireParser] = {}  # one parser per adapter instance


async def _discord_pre_route_hook(
    msg: InboundMessage, ctx: InboundContext, adapter: "DiscordAdapter"
) -> None:
    """Cold-path: lazy ThreadStore.is_owned warmup so revived threads route correctly.

    Mutates ``ctx.router.owned_threads`` in place when the DB confirms ownership.
    Bound with ``functools.partial(adapter=...)`` before passing to the pipeline.
    """
    meta = msg.platform_meta
    if not isinstance(meta, DiscordMeta):
        return
    if meta.thread_id is None:
        return  # not a thread
    if meta.thread_id in ctx.router.owned_threads:
        return  # hot set already knows
    if adapter._thread_store is None:
        return
    try:
        if await adapter._thread_store.is_owned(str(meta.thread_id), adapter._bot_id):
            ctx.router.owned_threads.add(meta.thread_id)
    except sqlite3.Error:
        # ThreadStore I/O failure — fall through; Router will DROP unrecognized thread
        log.warning(
            "pre_route_hook: ThreadStore.is_owned failed for thread %s",
            meta.thread_id,
            exc_info=True,
        )


async def _discord_pre_session_hook(  # noqa: C901 — DEBT:wiring-bootstrap-deps; verbatim extraction of auto-thread block
    msg: InboundMessage,
    ctx: InboundContext,
    raw_message: Any,
    adapter: "DiscordAdapter",
) -> InboundMessage:
    """Auto-create or claim a Discord thread when mention / watch-channel triggers it.

    Returns the (possibly updated) InboundMessage with DiscordMeta.thread_id
    set so downstream stages address the new thread.

    Must be bound with ``functools.partial(raw_message=..., adapter=...)`` before
    passing to ``InboundPipeline.run`` as ``pre_session_hook``.
    """
    meta = msg.platform_meta
    if not isinstance(meta, DiscordMeta):
        return msg

    _is_mention = msg.is_mention
    _is_dm = raw_message.guild is None
    _is_thread = isinstance(raw_message.channel, discord.Thread)
    _is_watch_channel = (
        not _is_dm
        and not _is_thread
        and raw_message.channel.id in adapter._watch_channels
    )

    # Auto-thread creation — creates a new thread from the message.
    resolved_thread_id: int | None = None
    if (
        adapter._auto_thread
        and (_is_mention or _is_watch_channel)
        and not isinstance(raw_message.channel, discord.Thread)
        and hasattr(raw_message.channel, "create_thread")
    ):
        try:
            thread = await raw_message.create_thread(
                name=make_thread_name(
                    raw_message.content, raw_message.author.display_name
                )
            )
            resolved_thread_id = thread.id
            ctx.router.owned_threads.add(thread.id)
            if adapter._thread_store is not None:
                await persist_thread_claim(
                    adapter._thread_store,
                    thread_id=thread.id,
                    bot_id=adapter._bot_id,
                    channel_id=raw_message.channel.id,
                    guild_id=getattr(raw_message.guild, "id", None),
                )
        except Exception:
            log.exception(
                "Failed to create Discord thread for message id=%s",
                raw_message.id,
            )
            # Discord may have created the thread despite the error —
            # recover thread_id to keep scope_id consistent.
            if hasattr(raw_message, "thread") and raw_message.thread is not None:
                resolved_thread_id = raw_message.thread.id
                ctx.router.owned_threads.add(raw_message.thread.id)
                if adapter._thread_store is not None:
                    await persist_thread_claim(
                        adapter._thread_store,
                        thread_id=raw_message.thread.id,
                        bot_id=adapter._bot_id,
                        channel_id=raw_message.channel.id,
                        guild_id=getattr(raw_message.guild, "id", None),
                    )
                    # persist_thread_claim already catches and logs failures internally.

    # Claim an existing thread when directly mentioned inside it.
    if _is_mention and isinstance(raw_message.channel, discord.Thread):
        ctx.router.owned_threads.add(raw_message.channel.id)
        if adapter._thread_store is not None:
            await persist_thread_claim(
                adapter._thread_store,
                thread_id=raw_message.channel.id,
                bot_id=adapter._bot_id,
                channel_id=getattr(
                    raw_message.channel, "parent_id", raw_message.channel.id
                ),
                guild_id=getattr(raw_message.guild, "id", None),
            )

    if resolved_thread_id is not None:
        new_scope_id = f"thread:{resolved_thread_id}"
        new_meta = dataclasses.replace(meta, thread_id=resolved_thread_id)
        new_routing = (
            dataclasses.replace(
                msg.routing,
                scope_id=new_scope_id,
                thread_id=str(resolved_thread_id),
                platform_meta=new_meta,
            )
            if msg.routing is not None
            else None
        )
        return dataclasses.replace(
            msg,
            scope_id=new_scope_id,
            platform_meta=new_meta,
            routing=new_routing,
        )
    return msg


def _resolve_send_to_id(message: Any) -> int:
    """Return the channel or thread id to target for typing cancellation."""
    if isinstance(message.channel, discord.Thread):
        return message.channel.id
    return message.channel.id


async def handle_message(adapter: "DiscordAdapter", message: Any) -> None:
    """Handle incoming Gateway message.

    Filters own/bot messages, dispatches audio/voice short-circuits,
    then delegates to InboundPipeline for the text path.
    """
    # Discard bot messages early — before normalization to avoid waste.
    if message.author.bot:
        return

    # Audio attachment short-circuit.
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
        return

    # Voice command dispatch — guild-only; runs before mention/DM filter.
    if message.guild is not None:
        if await adapter._handle_voice_command(message, TrustLevel.PUBLIC):
            return

    send_to_id = _resolve_send_to_id(message)
    adapter._start_typing(send_to_id)

    # Per-adapter parser — avoid recreating each message.
    parser = _parser_cache.get(id(adapter))
    if parser is None:
        parser = DiscordWireParser(adapter)
        _parser_cache[id(adapter)] = parser

    inbound_ctx = InboundContext(
        router=RouterCtx(
            bot_id=adapter._bot_id,
            owned_threads=adapter._owned_threads,  # mutable — shared by reference
            watch_channels=adapter._watch_channels if adapter._watch_channels else None,
        ),
        session=SessionCtx(
            turn_store=adapter._turn_store,
            thread_store=adapter._thread_store,
            thread_sessions_cache=adapter._thread_sessions,  # MUTABLE shared by ref
        ),
        dispatch=DispatchCtx(
            inbound_bus=adapter._inbound_bus,
            circuit_registry=adapter._circuit_registry,
            outbound_listener=adapter._outbound_listener,
            typing=adapter._typing,
            msg_catalog=adapter._msg_manager,
        ),
    )

    pre_route = functools.partial(_discord_pre_route_hook, adapter=adapter)
    pre_session = functools.partial(
        _discord_pre_session_hook, raw_message=message, adapter=adapter
    )

    async def _dc_backpressure(text: str) -> None:
        await message.reply(text)

    await _pipeline.run(
        message,
        inbound_ctx,
        parser,
        pre_route_hook=pre_route,
        pre_session_hook=pre_session,
        send_backpressure=_dc_backpressure,
        on_drop=lambda: adapter._cancel_typing(send_to_id),
    )
