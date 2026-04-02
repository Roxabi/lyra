"""Outbound message sending for the Telegram adapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from lyra.adapters.telegram_formatting import (
    _render_buttons,
    _render_text,
    _validate_inbound,
)
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
)
from lyra.core.render_events import ToolSummaryRenderEvent

if TYPE_CHECKING:
    from lyra.adapters._shared_streaming import PlatformCallbacks
    from lyra.adapters.telegram import TelegramAdapter

log = logging.getLogger("lyra.adapters.telegram")


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
# _typing_loop is a context-manager implementation kept for backwards
# compatibility — re-exported from telegram.py.
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


def build_streaming_callbacks(  # noqa: C901 — one closure per platform op
    adapter: "TelegramAdapter",
    original_msg: InboundMessage,
    outbound: OutboundMessage | None,
) -> "PlatformCallbacks":
    """Build PlatformCallbacks for StreamingSession from a TelegramAdapter context.

    Extracted from TelegramAdapter._make_streaming_callbacks to keep telegram.py
    under the 300-line file-length limit.
    """
    from lyra.adapters._shared_streaming import PlatformCallbacks

    meta = _validate_inbound(original_msg, "send_streaming")
    if meta is None:
        async def _noop_placeholder() -> tuple[None, None]:
            raise ValueError("invalid inbound message")

        async def _noop_fallback(text: str) -> None:
            return None

        return PlatformCallbacks(
            send_placeholder=_noop_placeholder,
            edit_placeholder_text=lambda ph, text: asyncio.sleep(0),
            edit_placeholder_tool=lambda ph, ev: asyncio.sleep(0),
            send_message=_noop_fallback,
            send_fallback=_noop_fallback,
            chunk_text=lambda text: [text],
            start_typing=lambda: None,
            cancel_typing=lambda: None,
        )

    chat_id, _, _ = meta
    reply_to: int | None = original_msg.platform_meta.get("message_id")
    _placeholder_text = adapter._msg("stream_placeholder", "\u2026")

    async def _send_placeholder() -> tuple[Any, int]:
        msg = await adapter.bot.send_message(
            chat_id=chat_id,
            text=_placeholder_text,
            **({"reply_to_message_id": reply_to} if reply_to is not None else {}),
        )
        return msg, msg.message_id

    async def _edit_placeholder_text(ph: Any, text: str) -> None:
        rendered = _render_text(text)
        if rendered:
            try:
                await adapter.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=ph.message_id,
                    text=rendered[0],
                    parse_mode="MarkdownV2",
                )
            except Exception as exc:
                log.debug("Placeholder text edit skipped: %s", exc)

    async def _edit_placeholder_tool(ph: Any, event: Any) -> None:
        summary = _format_tool_summary(event)
        rendered = _render_text(summary)
        if rendered:
            try:
                await adapter.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=ph.message_id,
                    text=rendered[0],
                    parse_mode="MarkdownV2",
                )
            except Exception as exc:
                log.debug("Tool summary edit skipped: %s", exc)

    async def _send_message(text: str) -> int | None:
        rendered = _render_text(text)
        last = None
        for chunk in rendered:
            try:
                last = await adapter.bot.send_message(
                    chat_id=chat_id, text=chunk, parse_mode="MarkdownV2"
                )
            except Exception:
                log.exception("Failed to send final text chunk")
        return last.message_id if last else None

    async def _send_fallback(text: str) -> int | None:
        """NOT adapter.send() — needs MarkdownV2 escaping."""
        rendered = _render_text(text) if text else []
        if not rendered:
            rendered = [text or _placeholder_text]
        last = None
        for chunk in rendered:
            last = await adapter.bot.send_message(
                chat_id=chat_id, text=chunk, parse_mode="MarkdownV2"
            )
        return last.message_id if last else None

    return PlatformCallbacks(
        send_placeholder=_send_placeholder,
        edit_placeholder_text=_edit_placeholder_text,
        edit_placeholder_tool=_edit_placeholder_tool,
        send_message=_send_message,
        send_fallback=_send_fallback,
        chunk_text=lambda text: _render_text(text) or [text],
        start_typing=lambda: adapter._start_typing(chat_id),
        cancel_typing=lambda: adapter._cancel_typing(chat_id),
    )

