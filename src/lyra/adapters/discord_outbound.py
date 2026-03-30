"""Outbound message sending for DiscordAdapter."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters._shared import DISCORD_MAX_LENGTH
from lyra.adapters.discord_formatting import render_buttons, render_text
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    OutboundMessage,
    Platform,
)
from lyra.core.render_events import RenderEvent, TextRenderEvent, ToolSummaryRenderEvent

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger("lyra.adapters.discord")

# Seconds between intermediate streaming edits (debounce).
# Aligned with Telegram. Respects Discord's ~1 edit/s rate limit per message.
STREAMING_EDIT_INTERVAL = 1.0


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


async def _discord_send_with_retry(
    coro_fn: Callable[[], Any],
    *,
    label: str,
    max_attempts: int = 3,
) -> None:
    """Call *coro_fn()* and retry with exponential backoff (1 s, 2 s, 4 s ...)."""
    for attempt in range(max_attempts):
        try:
            await coro_fn()
            return
        except Exception:
            if attempt == max_attempts - 1:
                log.exception("%s failed after %d attempts", label, max_attempts)
                return
            delay = 2**attempt  # 1 s, 2 s, 4 s ...
            log.warning(
                "%s failed (attempt %d/%d), retrying in %d s",
                label,
                attempt + 1,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)


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

    title = "🔧 Done ✅" if event.is_complete else "🔧 Working…"
    color = discord.Color.green() if event.is_complete else discord.Color.blue()
    description = "\n".join(format_tool_lines(event)) or "\u200b"
    return discord.Embed(title=title, description=description, color=color)


async def send_streaming(  # noqa: C901, PLR0915 — streaming protocol: tool-summary/text/fallback branches are inherently sequential
    adapter: "DiscordAdapter",
    original_msg: InboundMessage,
    events: AsyncIterator[RenderEvent],
    outbound: OutboundMessage | None = None,
) -> None:
    """Stream RenderEvent objects with embed-based tool summary and final text send.

    Flow for tool-using turns:
      placeholder → embed update on each ToolSummaryRenderEvent (throttled)
      → messageable.send(final text) on TextRenderEvent(is_final=True)

    Flow for text-only turns:
      placeholder → placeholder.edit(content=final text) on
      TextRenderEvent(is_final=True)

    Circuit breaker checks and recording are handled by OutboundDispatcher.
    """
    if original_msg.platform != Platform.DISCORD.value:
        log.error(
            "send_streaming() called with non-discord message id=%s",
            original_msg.id,
        )
        return

    channel_id: int | None = original_msg.platform_meta.get("channel_id")
    if channel_id is None:
        raise ValueError(
            "platform_meta missing required key 'channel_id' for send_streaming()"
        )
    thread_id: int | None = original_msg.platform_meta.get("thread_id")
    send_to_id: int = thread_id if thread_id is not None else channel_id
    messageable = await adapter._resolve_channel(send_to_id)

    parts: list[str] = []

    # Send placeholder
    _placeholder_text = adapter._msg("stream_placeholder", "\u2026")
    reply_msg_id: int | None = original_msg.platform_meta.get("message_id")
    should_reply = reply_msg_id is not None and thread_id is None
    try:
        if should_reply:
            msg_obj = messageable.get_partial_message(reply_msg_id)  # type: ignore[attr-defined]
            placeholder = await msg_obj.reply(_placeholder_text)
        else:
            placeholder = await messageable.send(_placeholder_text)
        if outbound is not None:
            outbound.metadata["reply_message_id"] = placeholder.id
    except Exception:
        log.exception("Failed to send placeholder — falling back to non-streaming")
        async for event in events:
            if isinstance(event, TextRenderEvent):
                parts.append(event.text)
        fallback_content = "".join(parts) or _placeholder_text
        fallback_outbound = OutboundMessage.from_text(fallback_content)
        await send(adapter, original_msg, fallback_outbound)
        if outbound is not None:
            outbound.metadata["reply_message_id"] = fallback_outbound.metadata.get(
                "reply_message_id"
            )
        return

    had_tool_events = False
    had_intermediate_text = False
    last_tool_edit: float | None = None
    last_intermediate_edit: float | None = None
    intermediate_text: str = ""
    final_text: str | None = None
    is_error_turn: bool = False
    stream_error: Exception | None = None

    try:
        async for event in events:
            if isinstance(event, ToolSummaryRenderEvent):
                had_tool_events = True
                # Don't overwrite intermediate text that is already visible in
                # the placeholder — the tool summary would erase what the user
                # can see.  The summary is still captured; the final text is
                # delivered as a new message (had_tool_events=True path below).
                if not had_intermediate_text:
                    now = time.monotonic()
                    if (
                        event.is_complete
                        or last_tool_edit is None
                        or (now - last_tool_edit) >= STREAMING_EDIT_INTERVAL
                    ):
                        embed = _build_tool_embed(event)
                        await _discord_send_with_retry(
                            lambda e=embed: placeholder.edit(content="", embed=e),
                            label="Tool summary embed",
                        )
                        last_tool_edit = now

            else:  # TextRenderEvent
                if event.is_final:
                    final_text = event.text
                    is_error_turn = event.is_error
                else:
                    # Intermediate text (between tool calls) — edit placeholder
                    # with accumulated text, debounced at STREAMING_EDIT_INTERVAL.
                    intermediate_text += event.text
                    now = time.monotonic()
                    if (
                        last_intermediate_edit is None
                        or (now - last_intermediate_edit) >= STREAMING_EDIT_INTERVAL
                    ):
                        display = intermediate_text[-DISCORD_MAX_LENGTH:]
                        await _discord_send_with_retry(
                            lambda d=display: placeholder.edit(content=d, embed=None),
                            label="Intermediate text edit",
                        )
                        last_intermediate_edit = now
                        had_intermediate_text = True
    except Exception as exc:
        stream_error = exc
        log.exception("Stream interrupted")

    # Deliver final text
    if final_text is not None:
        display_text = ("❌ " + final_text) if is_error_turn else final_text
        if stream_error is not None:
            if display_text:
                display_text += adapter._msg(
                    "stream_interrupted", " [response interrupted]"
                )
            else:
                display_text = adapter._msg("generic", GENERIC_ERROR_REPLY)
        final_chunks = (
            render_text(display_text, DISCORD_MAX_LENGTH) if display_text else []
        )
        if had_tool_events:
            # Tool summary stays in placeholder; text as new message(s).
            # Update reply_message_id to the final text message so session
            # routing can match user replies to the correct pool (#387).
            for i, chunk in enumerate(final_chunks):
                is_last = i == len(final_chunks) - 1
                if is_last:
                    try:
                        _sent = await messageable.send(chunk)
                        if outbound is not None:
                            outbound.metadata["reply_message_id"] = _sent.id
                    except Exception:
                        log.exception("Failed to send final text chunk")
                else:
                    await _discord_send_with_retry(
                        lambda c=chunk: messageable.send(c),
                        label="Final text chunk",
                    )
        elif final_chunks:
            # Text-only turn: edit placeholder with text
            await _discord_send_with_retry(
                lambda: placeholder.edit(content=final_chunks[0]),
                label="Final edit",
            )
            for extra_chunk in final_chunks[1:]:
                await _discord_send_with_retry(
                    lambda c=extra_chunk: messageable.send(c),
                    label="Overflow chunk",
                )
    elif stream_error is not None:
        error_text = adapter._msg("generic", GENERIC_ERROR_REPLY)
        await _discord_send_with_retry(
            lambda: placeholder.edit(content=error_text[:DISCORD_MAX_LENGTH]),
            label="Error edit",
        )

    # Cancel typing after final content is confirmed.
    if outbound is not None and outbound.intermediate:
        adapter._start_typing(send_to_id)
    else:
        adapter._cancel_typing(send_to_id)

    # Re-raise stream error so OutboundDispatcher can record CB failure
    if stream_error is not None:
        raise stream_error
