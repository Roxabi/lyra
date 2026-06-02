"""Outbound message sending for the Telegram adapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramAPIError

from factory.adapters.telegram.telegram_formatting import (
    _render_buttons,
    _render_text,
    _validate_inbound,
)
from factory.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
    TelegramMeta,
)
from factory.outbound.throttle import STREAMING_EDIT_INTERVAL

if TYPE_CHECKING:
    from factory.adapters.telegram import TelegramAdapter

log = logging.getLogger("factory.adapters.telegram")


# Implements ThrottleCapability Protocol from factory.outbound.throttle
class TelegramTypingIndicator:
    """ThrottleCapability impl — wraps adapter._start_typing/_cancel_typing."""

    edit_interval_s: float = STREAMING_EDIT_INTERVAL

    def __init__(self, adapter: "TelegramAdapter") -> None:
        self._adapter = adapter

    async def start_typing(self, scope_id: int) -> None:
        self._adapter._start_typing(scope_id)

    async def cancel_typing(self, scope_id: int) -> None:
        self._adapter._cancel_typing(scope_id)


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
        except TelegramAPIError as exc:
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
) -> AsyncGenerator[None, None]:
    """Send typing indicator immediately and refresh every *interval* seconds.

    Telegram expires the typing action after ~5s. The background task
    re-sends it every *interval* seconds until the context exits.
    stop_event.set() must precede task.cancel() for clean loop exit.
    """
    stop_event = asyncio.Event()
    try:
        await bot.send_chat_action(chat_id, "typing")
    except TelegramAPIError as exc:
        log.debug("typing indicator failed: %s", exc)

    async def keep_typing() -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                try:
                    await bot.send_chat_action(chat_id, "typing")
                except TelegramAPIError as exc:
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

    _pm = original_msg.platform_meta
    reply_to: int | None = _pm.message_id if isinstance(_pm, TelegramMeta) else None
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
