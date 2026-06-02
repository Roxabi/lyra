"""Inbound message handling for DiscordAdapter (on_message logic)."""

from __future__ import annotations

import dataclasses
import functools
import logging
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord

from factory.adapters.discord.discord_audio import handle_audio as _handle_audio
from factory.adapters.discord.discord_formatting import make_thread_name
from factory.adapters.discord.discord_threads import persist_thread_claim
from factory.adapters.shared._shared import AUDIO_MIME_TYPES
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import DiscordMeta, InboundMessage
from factory.inbound.attachment_ingest import (
    MAX_ATTACHMENT_INGEST_BYTES,
    AttachmentIngestError,
    AttachmentIngestStage,
)
from factory.inbound.context import DispatchCtx, InboundContext, RouterCtx, SessionCtx
from factory.inbound.dispatcher import Dispatcher
from factory.inbound.pipeline import InboundPipeline
from factory.inbound.router import Router
from factory.inbound.session_builder import SessionBuilder
from factory.inbound.wire_parser_discord import DiscordWireParser

if TYPE_CHECKING:
    from factory.adapters.discord import DiscordAdapter

log = logging.getLogger("factory.adapters.discord")

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
_parser_cache: dict[int, DiscordWireParser] = {}  # one parser per adapter instance


def build_discord_inbound_ctx(
    adapter: "DiscordAdapter",
    *,
    ingest: Any = None,
) -> InboundContext:
    """Shared InboundContext builder for text path (ingest=None) and audio path."""
    return InboundContext(
        router=RouterCtx(
            bot_id=adapter._bot_id,
            owned_threads=adapter._owned_threads,
            watch_channels=adapter._watch_channels if adapter._watch_channels else None,
        ),
        session=SessionCtx(
            turn_store=adapter._turn_store,
            thread_store=adapter._thread_store,
            thread_sessions_cache=adapter._thread_sessions,
        ),
        dispatch=DispatchCtx(
            inbound_bus=adapter._inbound_bus,
            circuit_registry=adapter._circuit_registry,
            outbound_listener=adapter._outbound_listener,
            typing=adapter._typing,
            msg_catalog=adapter._msg_manager,
        ),
        ingest=ingest,
    )


async def _discord_pre_route_hook(
    msg: InboundMessage, ctx: InboundContext, adapter: "DiscordAdapter"
) -> None:
    """Cold-path: lazy is_owned warmup; mutates owned_threads.  Bound via partial."""
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
    except (sqlite3.Error, RuntimeError):
        # sqlite3.Error: I/O failure; RuntimeError: DB not yet connected (_require_db).
        # Both cases: fall through; Router will DROP the unrecognized thread.
        log.warning(
            "pre_route_hook: ThreadStore.is_owned failed for thread %s",
            meta.thread_id,
            exc_info=True,
        )


@dataclass(frozen=True)
class AutoThreadDeps:
    """Frozen DI object for _try_auto_create_thread() — replaces PLR0913 param list."""

    raw_message: Any
    ctx: InboundContext
    adapter: "DiscordAdapter"
    is_mention: bool
    is_dm: bool
    is_thread: bool


async def _try_auto_create_thread(deps: AutoThreadDeps) -> int | None:
    """Auto-create a Discord thread when mention / watch-channel triggers it.

    Mutates ctx.router.owned_threads on success.
    Returns resolved thread_id, or None if not created.
    """
    raw_message, ctx, adapter, is_mention, is_dm, is_thread = (
        deps.raw_message,
        deps.ctx,
        deps.adapter,
        deps.is_mention,
        deps.is_dm,
        deps.is_thread,
    )
    _is_watch_channel = (
        not is_dm
        and not is_thread
        and raw_message.channel.id in adapter._watch_channels
    )
    if not (adapter._auto_thread and (is_mention or _is_watch_channel)):
        return None
    if isinstance(raw_message.channel, discord.Thread):
        return None
    if not hasattr(raw_message.channel, "create_thread"):
        return None

    try:
        thread = await raw_message.create_thread(
            name=make_thread_name(raw_message.content, raw_message.author.display_name)
        )
        ctx.router.owned_threads.add(thread.id)
        if adapter._thread_store is not None:
            await persist_thread_claim(
                adapter._thread_store,
                thread_id=thread.id,
                bot_id=adapter._bot_id,
                channel_id=raw_message.channel.id,
                guild_id=getattr(raw_message.guild, "id", None),
            )
        return thread.id
    except Exception:
        log.exception(
            "Failed to create Discord thread for message id=%s",
            raw_message.id,
        )
        # Discord may have created the thread despite the error —
        # recover thread_id to keep scope_id consistent.
        recovered = getattr(raw_message, "thread", None)
        if recovered is None:
            return None
        # NOTE: persist_thread_claim swallows its own exceptions silently
        # (see discord_threads.py), so a DB failure here is not observable.
        # The hot-set add may diverge from the DB transiently; the next
        # inbound message in this thread triggers _discord_pre_route_hook
        # which performs a cold-path is_owned lookup that reconciles state.
        # Eventual consistency is the documented contract.
        ctx.router.owned_threads.add(recovered.id)
        if adapter._thread_store is not None:
            await persist_thread_claim(
                adapter._thread_store,
                thread_id=recovered.id,
                bot_id=adapter._bot_id,
                channel_id=raw_message.channel.id,
                guild_id=getattr(raw_message.guild, "id", None),
            )
        return recovered.id


async def _claim_existing_thread(
    raw_message: Any,
    ctx: InboundContext,
    adapter: "DiscordAdapter",
    *,
    is_mention: bool,
) -> None:
    """Claim an existing thread when bot is mentioned inside it."""
    if not (is_mention and isinstance(raw_message.channel, discord.Thread)):
        return
    ctx.router.owned_threads.add(raw_message.channel.id)
    if adapter._thread_store is None:
        return
    await persist_thread_claim(
        adapter._thread_store,
        thread_id=raw_message.channel.id,
        bot_id=adapter._bot_id,
        channel_id=getattr(raw_message.channel, "parent_id", raw_message.channel.id),
        guild_id=getattr(raw_message.guild, "id", None),
    )


async def _discord_pre_session_hook(
    msg: InboundMessage,
    ctx: InboundContext,
    raw_message: Any,
    adapter: "DiscordAdapter",
) -> InboundMessage:
    """Pre-session hook: auto-thread create + claim; returns updated InboundMessage.

    Must be bound with ``functools.partial(raw_message=..., adapter=...)`` before
    passing to ``InboundPipeline.run`` as ``pre_session_hook``.
    """
    meta = msg.platform_meta
    if not isinstance(meta, DiscordMeta):
        return msg

    is_mention = msg.is_mention
    is_dm = raw_message.guild is None
    is_thread = isinstance(raw_message.channel, discord.Thread)

    resolved_thread_id = await _try_auto_create_thread(
        AutoThreadDeps(
            raw_message=raw_message,
            ctx=ctx,
            adapter=adapter,
            is_mention=is_mention,
            is_dm=is_dm,
            is_thread=is_thread,
        )
    )
    await _claim_existing_thread(raw_message, ctx, adapter, is_mention=is_mention)

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


async def _warn_oversize_reply(message: Any) -> None:
    """Send oversize warning reply, swallowing HTTP errors."""
    try:
        await message.reply("That file is too large to process.")
    except discord.HTTPException:
        log.warning(
            "Failed to send oversize reply for message id=%s",
            message.id,
            exc_info=True,
        )


async def handle_message(adapter: "DiscordAdapter", message: Any) -> None:
    """Handle incoming Gateway message.

    Bot filter → audio/voice short-circuits → InboundPipeline (text path).
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

    # Oversize attachment pre-filter (T9 / T10).
    raw_atts = getattr(message, "attachments", None) or []
    oversize_count = sum(
        1
        for a in raw_atts
        if (getattr(a, "size", None) or 0) > MAX_ATTACHMENT_INGEST_BYTES
    )
    if oversize_count:
        await _warn_oversize_reply(message)
    if (
        oversize_count
        and oversize_count == len(raw_atts)
        and not (message.content or "").strip()
    ):
        # T10: all attachments oversize + no text → drop gracefully
        return

    # Use channel.id (not thread.id — auto-thread is created later by pre_session_hook).
    send_to_id = message.channel.id
    adapter._start_typing(send_to_id)

    # Per-adapter parser — avoid recreating each message.
    parser = _parser_cache.get(id(adapter))
    if parser is None:
        parser = DiscordWireParser(adapter)
        _parser_cache[id(adapter)] = parser

    _ingest = getattr(adapter, "_ingest_ctx", None)
    inbound_ctx = build_discord_inbound_ctx(adapter, ingest=_ingest)

    pre_route = functools.partial(_discord_pre_route_hook, adapter=adapter)
    pre_session = functools.partial(
        _discord_pre_session_hook, raw_message=message, adapter=adapter
    )

    async def _dc_backpressure(text: str) -> None:
        await message.reply(text)

    try:
        await _pipeline.run(
            message,
            inbound_ctx,
            parser,
            pre_route_hook=pre_route,
            pre_session_hook=pre_session,
            send_backpressure=_dc_backpressure,
            on_drop=lambda: adapter._cancel_typing(send_to_id),
        )
    except AttachmentIngestError as e:
        await message.reply(e.user_message)
