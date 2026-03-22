"""Outbound message sending for DiscordAdapter."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

from lyra.adapters._shared import DISCORD_MAX_LENGTH
from lyra.adapters.discord_formatting import render_buttons, render_text
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    OutboundMessage,
    Platform,
)

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger("lyra.adapters.discord")

# Seconds between intermediate streaming edits (debounce).
# Aligned with Telegram. Respects Discord's ~1 edit/s rate limit per message.
STREAMING_EDIT_INTERVAL = 1.0


async def _discord_typing_worker(  # noqa: C901 — retry + error branches
    resolve_channel: Callable,
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


async def send_streaming(  # noqa: C901, PLR0915 — streaming protocol: edit/chunk/finalize branches are inherently sequential
    adapter: "DiscordAdapter",
    original_msg: InboundMessage,
    chunks: AsyncIterator[str],
    outbound: OutboundMessage | None = None,
) -> None:
    """Stream response with edit-in-place, debounced at ~1s."""
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
        async for chunk in chunks:
            parts.append(chunk)
        fallback_content = "".join(parts) or _placeholder_text
        fallback_outbound = OutboundMessage.from_text(fallback_content)
        await send(adapter, original_msg, fallback_outbound)
        if outbound is not None:
            outbound.metadata["reply_message_id"] = fallback_outbound.metadata.get(
                "reply_message_id"
            )
        return

    last_edit: float | None = None  # set on first token; debounce starts then
    stream_error: Exception | None = None
    try:
        async for chunk in chunks:
            parts.append(chunk)
            now = time.monotonic()
            if last_edit is None:
                last_edit = now
            elif now - last_edit >= STREAMING_EDIT_INTERVAL:
                accumulated = "".join(parts)
                await placeholder.edit(content=accumulated[:DISCORD_MAX_LENGTH])
                last_edit = now
    except Exception as exc:
        stream_error = exc
        log.exception("Stream interrupted")

    accumulated = "".join(parts)
    if stream_error is not None:
        if accumulated:
            accumulated += adapter._msg("stream_interrupted", " [response interrupted]")
        else:
            accumulated = adapter._msg("generic", GENERIC_ERROR_REPLY)

    # Final edit (always runs): edit placeholder with first chunk, send overflow.
    if accumulated:
        final_chunks = render_text(accumulated, DISCORD_MAX_LENGTH)
        await _discord_send_with_retry(
            lambda: placeholder.edit(content=final_chunks[0]),
            label="Final edit",
        )
        for extra_chunk in final_chunks[1:]:
            await _discord_send_with_retry(
                lambda c=extra_chunk: messageable.send(c),
                label="Overflow chunk",
            )

    # Cancel typing after final content is confirmed (streaming done).
    # If this was an intermediate message, restart the indicator so the user
    # knows more content is coming (mirrors the same check in send()).
    if outbound is not None and outbound.intermediate:
        adapter._start_typing(send_to_id)
    else:
        adapter._cancel_typing(send_to_id)

    # Re-raise stream error so OutboundDispatcher can record CB failure
    if stream_error is not None:
        raise stream_error
