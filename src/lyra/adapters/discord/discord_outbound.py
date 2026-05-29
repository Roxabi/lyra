"""Outbound message sending for DiscordAdapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import discord

from lyra.adapters.discord.discord_formatting import (
    _validate_inbound,
    render_buttons,
    render_text,
)
from lyra.adapters.shared._shared import (
    DISCORD_MAX_LENGTH,
)
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
)
from lyra.outbound.throttle import STREAMING_EDIT_INTERVAL

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger("lyra.adapters.discord")

# Channels that support get_partial_message(); _resolve_channel() returns one of these.
_PartialMessageable = (
    discord.TextChannel
    | discord.Thread
    | discord.DMChannel
    | discord.VoiceChannel
    | discord.StageChannel
)


# Implements ThrottleCapability Protocol from lyra.outbound.throttle.
class DiscordTypingIndicator:
    """ThrottleCapability impl for Discord — wraps adapter._start_typing/_cancel_typing.

    Composed into OutboundEmitter by DiscordAdapter._make_emitter (T19 / Slice 5).
    Holds no state beyond the back-reference to the adapter.
    """

    edit_interval_s: float = STREAMING_EDIT_INTERVAL

    def __init__(self, adapter: "DiscordAdapter") -> None:
        self._adapter = adapter

    async def start_typing(self, scope_id: int) -> None:
        self._adapter._start_typing(scope_id)

    async def cancel_typing(self, scope_id: int) -> None:
        self._adapter._cancel_typing(scope_id)


async def _discord_typing_worker(  # noqa: C901 — DEBT:adapter-dispatch-complexity
    resolve_channel: Callable[..., Any],
    channel_id: int,
) -> None:
    """Hold Discord typing indicator for channel_id until cancelled.

    Sends trigger_typing() every 9 s (Discord expires after ~10 s). Uses a
    manual loop instead of channel.typing() to avoid the built-in 5 s refresh
    which triggers 429s when many conversations run in parallel.
    """
    try:
        # Retry: newly-created threads may not be cached immediately.
        channel = None
        for _attempt in range(3):
            try:
                channel = await resolve_channel(channel_id)
                break
            except Exception as exc:  # noqa: BLE001 — typing-worker resolve retry, type sanitized on warn-and-raise
                if _attempt == 2:
                    log.warning(
                        "typing: failed to resolve channel %d after"
                        " %d attempts: type=%s",
                        channel_id,
                        _attempt + 1,
                        type(exc).__name__,
                    )
                    raise
                await asyncio.sleep(1.0 * (2**_attempt))
        if channel is None:
            raise RuntimeError("typing: channel not resolved after 3 attempts")
        _consecutive_errors = 0
        while True:
            try:
                await channel.typing()
                _consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except discord.HTTPException as exc:
                _consecutive_errors += 1
                if _consecutive_errors == 1:
                    log.warning(
                        "typing: channel %d trigger failed: %s — will retry",
                        channel_id,
                        exc,
                    )
                if _consecutive_errors >= 3:
                    log.warning(
                        "typing: channel %d giving up after %d consecutive errors",
                        channel_id,
                        _consecutive_errors,
                    )
                    return
            await asyncio.sleep(9)
    except asyncio.CancelledError:
        pass
    except discord.HTTPException as exc:
        log.warning(
            "typing: worker for channel %d exited unexpectedly: %s",
            channel_id,
            exc,
        )


async def send(  # noqa: C901 — DEBT:adapter-dispatch-complexity
    adapter: "DiscordAdapter",
    original_msg: InboundMessage,
    outbound: OutboundMessage,
) -> None:
    """Send response back to Discord."""
    meta = _validate_inbound(original_msg, "send")
    if meta is None:
        return
    channel_id, thread_id, message_id = meta
    send_to_id: int = thread_id if thread_id is not None else channel_id
    messageable = await adapter._resolve_channel(send_to_id)

    text = outbound.to_text()
    chunks = render_text(text, DISCORD_MAX_LENGTH)
    view = render_buttons(outbound.buttons)
    last_idx = len(chunks) - 1

    # Skip reply-to in threads — thread context makes it redundant.
    reply_msg_id: int | None = message_id
    should_reply = reply_msg_id is not None and thread_id is None
    for i, chunk in enumerate(chunks):
        chunk_view = view if (i == last_idx and view is not None) else None
        if should_reply:
            if reply_msg_id is None:
                raise RuntimeError(
                    "reply_msg_id must not be None when should_reply is True"
                )
            msg_obj = cast(_PartialMessageable, messageable).get_partial_message(
                reply_msg_id
            )
            if chunk_view is not None:
                sent = await msg_obj.reply(chunk, view=chunk_view)
            else:
                sent = await msg_obj.reply(chunk)
        else:
            if chunk_view is not None:
                sent = await messageable.send(chunk, view=chunk_view)
            else:
                sent = await messageable.send(chunk)
        if i == last_idx:
            outbound.metadata["reply_message_id"] = sent.id
    if outbound.intermediate:
        adapter._start_typing(send_to_id)
    else:
        adapter._cancel_typing(send_to_id)
    log.debug(
        "stored reply_message_id=%s for msg_id=%s",
        outbound.metadata.get("reply_message_id"),
        original_msg.id,
    )
