"""Outbound message sending for DiscordAdapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters._shared import (
    DISCORD_MAX_LENGTH,
    format_tool_summary_header,
)
from lyra.adapters.discord_formatting import render_buttons, render_text
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
)
from lyra.core.render_events import ToolSummaryRenderEvent

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger("lyra.adapters.discord")


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
        assert channel is not None  # guaranteed by the loop above
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
    if original_msg.platform != Platform.DISCORD.value:
        log.error(
            "send() called with non-discord message id=%s",
            original_msg.id,
        )
        return

    channel_id: int | None = original_msg.platform_meta.get("channel_id")
    if channel_id is None:
        raise ValueError("platform_meta missing required key 'channel_id' for send()")
    thread_id: int | None = original_msg.platform_meta.get("thread_id")
    send_to_id: int = thread_id if thread_id is not None else channel_id
    messageable = await adapter._resolve_channel(send_to_id)

    text = outbound.to_text()
    chunks = render_text(text, DISCORD_MAX_LENGTH)
    view = render_buttons(outbound.buttons)
    last_idx = len(chunks) - 1

    # Skip reply-to in threads — thread context makes it redundant.
    reply_msg_id: int | None = original_msg.platform_meta.get("message_id")
    should_reply = reply_msg_id is not None and thread_id is None
    for i, chunk in enumerate(chunks):
        chunk_view = view if (i == last_idx and view is not None) else None
        if should_reply:
            msg_obj = messageable.get_partial_message(reply_msg_id)  # type: ignore[attr-defined]
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
    from lyra.core.tool_recap_format import format_tool_lines

    title = format_tool_summary_header(event)
    color = discord.Color.green() if event.is_complete else discord.Color.blue()
    description = "\n".join(format_tool_lines(event)) or "\u200b"
    return discord.Embed(title=title, description=description, color=color)
