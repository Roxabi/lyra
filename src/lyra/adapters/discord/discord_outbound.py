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
    format_tool_summary_header,
)
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
)
from lyra.core.messaging.render_events import ToolSummaryRenderEvent

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.shared._shared_streaming import PlatformCallbacks

log = logging.getLogger("lyra.adapters.discord")

# Channels that support get_partial_message(); _resolve_channel() returns one of these.
_PartialMessageable = (
    discord.TextChannel
    | discord.Thread
    | discord.DMChannel
    | discord.VoiceChannel
    | discord.StageChannel
)


async def _discord_typing_worker(  # noqa: C901 — retry + error branches
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
            except Exception as exc:
                if _attempt == 2:
                    log.warning(
                        "typing: failed to resolve channel %d after %d attempts: %s",
                        channel_id,
                        _attempt + 1,
                        exc,
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
            except Exception as exc:
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
    except Exception as exc:
        log.warning(
            "typing: worker for channel %d exited unexpectedly: %s",
            channel_id,
            exc,
        )


async def send(  # noqa: C901 — attachment loop adds branches
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


def _build_tool_embed(event: ToolSummaryRenderEvent) -> "discord.Embed":
    """Build a Discord embed from a ToolSummaryRenderEvent."""
    from lyra.core.messaging.tool_recap_format import format_tool_lines

    title = format_tool_summary_header(event)
    color = discord.Color.green() if event.is_complete else discord.Color.blue()
    description = "\n".join(format_tool_lines(event)) or "\u200b"
    return discord.Embed(title=title, description=description, color=color)


def build_streaming_callbacks(  # noqa: C901 — one closure per platform op
    adapter: "DiscordAdapter",
    original_msg: InboundMessage,
    outbound: OutboundMessage | None,
) -> "PlatformCallbacks":
    """Build PlatformCallbacks for StreamingSession from a DiscordAdapter context.

    Extracted from DiscordAdapter._make_streaming_callbacks to keep discord.py
    under the 300-line file-length limit, matching the Telegram pattern in
    telegram_outbound.build_streaming_callbacks().
    """
    from lyra.adapters.shared._shared import send_with_retry
    from lyra.adapters.shared._shared_streaming import PlatformCallbacks

    meta = _validate_inbound(original_msg, "build_streaming_callbacks")
    if meta is None:

        async def _bad_placeholder():
            raise ValueError("not a discord message")

        async def _noop(text=""):
            return None

        return PlatformCallbacks(
            send_placeholder=_bad_placeholder,
            edit_placeholder_text=lambda ph, text: asyncio.sleep(0),
            edit_placeholder_tool=lambda ph, ev, h: asyncio.sleep(0),
            send_message=_noop,
            send_fallback=_noop,
            chunk_text=lambda t: [t],
            start_typing=lambda: None,
            cancel_typing=lambda: None,
            get_msg=lambda key, fallback: fallback,
            placeholder_text="\u2026",
        )

    channel_id, thread_id, message_id = meta
    send_to_id: int = thread_id if thread_id is not None else channel_id
    reply_msg_id: int | None = message_id
    should_reply = reply_msg_id is not None and thread_id is None
    _placeholder_text = adapter._msg("stream_placeholder", "\u2026")

    async def _send_placeholder():
        messageable = await adapter._resolve_channel(send_to_id)
        if should_reply:
            if reply_msg_id is None:
                raise RuntimeError(
                    "reply_msg_id must not be None when should_reply is True"
                )
            msg_obj = cast(_PartialMessageable, messageable).get_partial_message(
                reply_msg_id
            )
            placeholder = await msg_obj.reply(_placeholder_text)
        else:
            placeholder = await messageable.send(_placeholder_text)
        return placeholder, placeholder.id

    async def _edit_placeholder_text(ph, text):
        display = text[-DISCORD_MAX_LENGTH:]
        await send_with_retry(
            lambda d=display: ph.edit(content=d, embed=None),
            label="Intermediate text edit",
        )

    async def _edit_placeholder_tool(ph, event, header: str = ""):
        embed = _build_tool_embed(event)
        await send_with_retry(
            lambda e=embed: ph.edit(content="", embed=e),
            label="Tool summary embed",
        )

    async def _send_message(text: str) -> int | None:
        messageable = await adapter._resolve_channel(send_to_id)
        chunks = render_text(text, DISCORD_MAX_LENGTH)
        last_id = None
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            if is_last:
                try:
                    sent = await messageable.send(chunk)
                    last_id = sent.id
                except Exception:  # noqa: BLE001 — resilient: caller has no fallback for partial streams
                    log.exception("Failed to send final chunk to Discord")
            else:
                await send_with_retry(
                    lambda c=chunk: messageable.send(c),
                    label="Final text chunk",
                )
        return last_id

    async def _send_fallback(text: str) -> int | None:
        fallback_outbound = (
            OutboundMessage.from_text(text)
            if text
            else OutboundMessage.from_text(_placeholder_text)
        )
        await send(adapter, original_msg, fallback_outbound)
        return fallback_outbound.metadata.get("reply_message_id")

    return PlatformCallbacks(
        send_placeholder=_send_placeholder,
        edit_placeholder_text=_edit_placeholder_text,
        edit_placeholder_tool=_edit_placeholder_tool,
        send_message=_send_message,
        send_fallback=_send_fallback,
        chunk_text=lambda text: render_text(text, DISCORD_MAX_LENGTH),
        start_typing=lambda: adapter._start_typing(send_to_id),
        cancel_typing=lambda: adapter._cancel_typing(send_to_id),
        get_msg=adapter._msg,
        placeholder_text=_placeholder_text,
    )
