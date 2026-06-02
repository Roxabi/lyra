"""Outbound message sending for DiscordAdapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import discord

from factory.adapters.discord.discord_formatting import (
    _validate_inbound,
    render_buttons,
    render_text,
)
from factory.adapters.shared._shared import (
    DISCORD_MAX_LENGTH,
)
from factory.adapters.shared.platform_send import SendContext, send_chunked_message
from factory.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
)
from factory.outbound.throttle import STREAMING_EDIT_INTERVAL

if TYPE_CHECKING:
    from factory.adapters.discord import DiscordAdapter

log = logging.getLogger("factory.adapters.discord")

# Channels that support get_partial_message(); _resolve_channel() returns one of these.
#
# Discord-specific: only these channel types expose get_partial_message(), which is
# required for the reply-to path (should_reply=True, thread_id=None). The cast is
# kept inline in send() rather than pushed into platform_send because it couples
# tightly to the Discord SDK type hierarchy — platform_send stays SDK-agnostic.
_PartialMessageable = (
    discord.TextChannel
    | discord.Thread
    | discord.DMChannel
    | discord.VoiceChannel
    | discord.StageChannel
)


# Implements ThrottleCapability Protocol from factory.outbound.throttle.
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
    """Send response back to Discord.

    Discord-specific send logic:
    - reply-vs-thread: skip reply-to when inside a thread (thread context makes
      reply redundant). ``should_reply`` encodes this gate.
    - cast to ``_PartialMessageable``: required for ``get_partial_message()`` on
      the reply path; kept inline (not in platform_send) because it couples to
      the Discord SDK type hierarchy.

    The per-chunk iteration is delegated to ``send_chunked_message`` from
    ``platform_send``, which owns the view-on-last logic shared with Telegram.
    The reply-to-on-all-chunks path is Discord-only and stays in ``_send_chunk``.
    """
    meta = _validate_inbound(original_msg, "send")
    if meta is None:
        return
    channel_id, thread_id, message_id = meta
    send_to_id: int = thread_id if thread_id is not None else channel_id
    messageable = await adapter._resolve_channel(send_to_id)

    text = outbound.to_text()
    chunks = render_text(text, DISCORD_MAX_LENGTH)
    view = render_buttons(outbound.buttons)

    # Skip reply-to in threads — thread context makes it redundant.
    reply_msg_id: int | None = message_id
    should_reply = reply_msg_id is not None and thread_id is None

    async def _send_chunk(
        chunk: str,
        is_first: bool,
        is_last: bool,
        buttons: Any,
    ) -> int:
        """Send one chunk, matching ChunkSender protocol from platform_send.

        platform_send passes buttons only on the last chunk (buttons=None otherwise).
        When should_reply is True, every chunk is sent as a reply — this matches
        original behaviour where msg_obj.reply() was used for all chunks.
        """
        del is_first  # reply-on-all-chunks: is_first not needed for Discord logic
        if should_reply:
            if reply_msg_id is None:
                raise RuntimeError(
                    "reply_msg_id must not be None when should_reply is True"
                )
            msg_obj = cast(_PartialMessageable, messageable).get_partial_message(
                reply_msg_id
            )
            if is_last and buttons is not None:
                sent = await msg_obj.reply(chunk, view=buttons)
            else:
                sent = await msg_obj.reply(chunk)
        else:
            if is_last and buttons is not None:
                sent = await messageable.send(chunk, view=buttons)
            else:
                sent = await messageable.send(chunk)
        return sent.id

    ctx = SendContext(
        chunks=chunks,
        buttons=view,
        outbound=outbound,
        adapter=adapter,
        scope_id=send_to_id,
    )
    await send_chunked_message(ctx, _send_chunk)

    log.debug(
        "stored reply_message_id=%s for msg_id=%s",
        outbound.metadata.get("reply_message_id"),
        original_msg.id,
    )
