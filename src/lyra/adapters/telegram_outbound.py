"""Outbound message sending for the Telegram adapter."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from lyra.adapters.telegram_formatting import (
    _render_buttons,
    _render_text,
    _validate_inbound,
)
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    OutboundMessage,
)
from lyra.core.render_events import RenderEvent, TextRenderEvent, ToolSummaryRenderEvent

if TYPE_CHECKING:
    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger("lyra.adapters.telegram")

# Seconds between intermediate streaming edits (debounce).
# Aligned with Discord. Telegram allows ~1 edit/s per chat before flood-wait.
STREAMING_EDIT_INTERVAL = 1.0


# ---------------------------------------------------------------------------
# Typing indicator — two-phase design
#
# Phase 1 (message receipt): _start_typing() creates a _typing_worker Task that
#   fires send_chat_action every 3s. This starts immediately when a message is
#   received by _on_message / _on_voice_message, before any processing begins.
#
# Phase 2 (response send): _cancel_typing() stops the task. For regular replies
#   (send()) this happens at the start of send(). For streaming replies
#   (send_streaming()) the task runs until the first chunk arrives.
#
# _typing_loop is the original context-manager implementation used by
# send_streaming for the streaming phase itself (typing while chunks are sent).
# ---------------------------------------------------------------------------
async def _typing_worker(bot: Any, chat_id: int, interval: float = 3.0) -> None:
    """Continuously refresh the Telegram typing indicator for chat_id.

    Sends 'typing' chat action immediately, then repeats every *interval*
    seconds until cancelled. Telegram expires the indicator after ~5s so
    the interval must stay well below that (default 3.0s gives a 2s buffer).

    Stops automatically after 3 consecutive send_chat_action failures to avoid
    hammering a blocked/deleted chat.
    """
    consecutive_failures = 0
    while True:
        try:
            await bot.send_chat_action(chat_id, "typing")
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            log.debug("typing worker: %s (failure %d/3)", exc, consecutive_failures)
            if consecutive_failures >= 3:
                log.warning(
                    "typing worker for chat %d: stopping after 3 consecutive failures",
                    chat_id,
                )
                break
        await asyncio.sleep(interval)


@asynccontextmanager
async def _typing_loop(
    bot: Any,
    chat_id: int,
    interval: float = 3.0,
) -> AsyncIterator[None]:
    """Send typing indicator immediately and refresh every *interval* seconds.

    Telegram expires the typing action after ~5s. The background task
    re-sends it every *interval* seconds until the context exits.
    stop_event.set() must precede task.cancel() for clean loop exit.
    """
    stop_event = asyncio.Event()
    try:
        await bot.send_chat_action(chat_id, "typing")
    except Exception as exc:
        log.debug("typing indicator failed: %s", exc)

    async def keep_typing() -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                try:
                    await bot.send_chat_action(chat_id, "typing")
                except Exception as exc:
                    log.debug("typing indicator failed: %s", exc)

    task = asyncio.create_task(keep_typing())
    try:
        yield
    finally:
        stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def send(
    adapter: TelegramAdapter,
    original_msg: InboundMessage,
    outbound: OutboundMessage,
) -> None:
    """Send a response back to Telegram via bot.send_message.

    Circuit breaker checks and recording are handled by OutboundDispatcher,
    not here. This method performs the bare send and raises on failure.
    """
    meta = _validate_inbound(original_msg, "send")
    if meta is None:
        return
    chat_id, _, _ = meta

    # Flatten content parts to plain text, escape and chunk
    text = outbound.to_text()
    chunks = _render_text(text)
    keyboard = _render_buttons(outbound.buttons)
    last_idx = len(chunks) - 1

    reply_to: int | None = original_msg.platform_meta.get("message_id")
    for i, chunk in enumerate(chunks):
        kwargs: dict = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "MarkdownV2",
        }
        if i == 0 and reply_to is not None:
            kwargs["reply_to_message_id"] = reply_to
        if i == last_idx and keyboard is not None:
            kwargs["reply_markup"] = keyboard
        sent = await adapter.bot.send_message(**kwargs)
        if i == last_idx:
            outbound.metadata["reply_message_id"] = sent.message_id
    if outbound.intermediate:
        adapter._start_typing(chat_id)
    else:
        adapter._cancel_typing(chat_id)


def _format_tool_summary(event: ToolSummaryRenderEvent) -> str:
    """Format a ToolSummaryRenderEvent as human-readable Telegram text."""
    from lyra.core.tool_recap_format import format_tool_lines

    header = "🔧 Done ✅" if event.is_complete else "🔧 Working…"
    body = "\n".join(format_tool_lines(event))
    return f"{header}\n{body}".strip() if body else header


async def send_streaming(  # noqa: C901, PLR0915 — streaming protocol: tool-summary/text/fallback branches are inherently sequential
    adapter: TelegramAdapter,
    original_msg: InboundMessage,
    events: AsyncIterator[RenderEvent],
    outbound: OutboundMessage | None = None,
) -> None:
    """Stream RenderEvent objects with edit-in-place tool summary and final text send.

    Flow for tool-using turns:
      placeholder → editMessage(tool summary) on each ToolSummaryRenderEvent
      → send_message(final text) on TextRenderEvent(is_final=True)

    Flow for text-only turns:
      placeholder → editMessage(final text) on TextRenderEvent(is_final=True)

    Circuit breaker checks and recording are handled by OutboundDispatcher,
    not here. This method performs the bare streaming send and raises on failure.

    When *outbound* is provided, ``outbound.metadata["reply_message_id"]``
    is set to the placeholder message ID after it is sent.
    """
    meta = _validate_inbound(original_msg, "send_streaming")
    if meta is None:
        return
    chat_id, _, _ = meta

    # The typing task was started by _start_typing() in _on_message on receipt.
    # We let it run until the placeholder is sent, then cancel it.
    parts: list[str] = []

    # Send placeholder
    _placeholder_text = adapter._msg("stream_placeholder", "\u2026")
    reply_to: int | None = original_msg.platform_meta.get("message_id")
    try:
        placeholder = await adapter.bot.send_message(
            chat_id=chat_id,
            text=_placeholder_text,
            **({"reply_to_message_id": reply_to} if reply_to is not None else {}),
        )
        if outbound is not None:
            outbound.metadata["reply_message_id"] = placeholder.message_id
    except Exception:
        adapter._cancel_typing(chat_id)
        log.exception("Failed to send placeholder — falling back to non-streaming")
        async for event in events:
            if isinstance(event, TextRenderEvent):
                parts.append(event.text)
        fallback_content = "".join(parts) or _placeholder_text
        chunks_rendered = _render_text(fallback_content)
        if chunks_rendered:
            fallback_msg = None
            for rendered_chunk in chunks_rendered:
                fallback_msg = await adapter.bot.send_message(
                    chat_id=chat_id,
                    text=rendered_chunk,
                    parse_mode="MarkdownV2",
                )
        else:
            fallback_msg = await adapter.bot.send_message(
                chat_id=chat_id, text=fallback_content
            )
        if outbound is not None and fallback_msg is not None:
            outbound.metadata["reply_message_id"] = fallback_msg.message_id
        return

    had_tool_events = False
    last_tool_edit: float | None = None
    final_text: str | None = None
    is_error_turn: bool = False
    stream_error: Exception | None = None

    try:
        async for event in events:
            if isinstance(event, ToolSummaryRenderEvent):
                had_tool_events = True
                tool_text = _format_tool_summary(event)
                now = time.monotonic()
                # Always edit on is_complete; otherwise respect debounce
                if (
                    event.is_complete
                    or last_tool_edit is None
                    or (now - last_tool_edit) >= STREAMING_EDIT_INTERVAL
                ):
                    try:
                        rendered = _render_text(tool_text)
                        await adapter.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=placeholder.message_id,
                            text=rendered[0],
                            parse_mode="MarkdownV2",
                        )
                        last_tool_edit = now
                    except Exception as edit_exc:
                        log.debug("Tool summary edit skipped: %s", edit_exc)

            elif isinstance(event, TextRenderEvent) and event.is_final:  # pyright: ignore[reportUnnecessaryIsInstance]
                final_text = event.text
                is_error_turn = event.is_error
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
        final_chunks = _render_text(display_text) if display_text else []
        if had_tool_events:
            # Tool summary stays in placeholder; text sent as new message(s).
            # Update reply_message_id to the final text message so session
            # routing can match user replies to the correct pool (#387).
            _last_sent = None
            for chunk in final_chunks:
                try:
                    _last_sent = await adapter.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        parse_mode="MarkdownV2",
                    )
                except Exception:
                    log.exception("Failed to send final text chunk")
            if outbound is not None and _last_sent is not None:
                outbound.metadata["reply_message_id"] = _last_sent.message_id
        elif final_chunks:
            # Text-only turn: edit placeholder with text (preserves overflow logic)
            try:
                await adapter.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=placeholder.message_id,
                    text=final_chunks[0],
                    parse_mode="MarkdownV2",
                )
            except Exception:
                log.exception("Final edit failed")
            for extra_chunk in final_chunks[1:]:
                try:
                    await adapter.bot.send_message(
                        chat_id=chat_id,
                        text=extra_chunk,
                        parse_mode="MarkdownV2",
                    )
                except Exception:
                    log.exception("Failed to send overflow chunk")
    elif stream_error is not None:
        # No text at all — send generic error
        error_text = adapter._msg("generic", GENERIC_ERROR_REPLY)
        try:
            await adapter.bot.edit_message_text(
                chat_id=chat_id,
                message_id=placeholder.message_id,
                text=_render_text(error_text)[0],
                parse_mode="MarkdownV2",
            )
        except Exception:
            log.exception("Error edit failed")

    # Cancel typing after final content is confirmed.
    if outbound is not None and outbound.intermediate:
        adapter._start_typing(chat_id)
    else:
        adapter._cancel_typing(chat_id)

    # Re-raise stream error so OutboundDispatcher can record CB failure
    if stream_error is not None:
        raise stream_error
