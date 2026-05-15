"""Outbound message sending for the Telegram adapter."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramAPIError

from lyra.adapters.shared._shared_streaming_state import STREAMING_EDIT_INTERVAL
from lyra.adapters.telegram.telegram_formatting import (
    _render_buttons,
    _render_text,
    _validate_inbound,
)
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
    TelegramMeta,
)
from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)

if TYPE_CHECKING:
    from lyra.adapters.shared._shared_streaming import PlatformCallbacks
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
) -> AsyncIterator[None]:
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


def _dim_italic(text: str) -> str:
    """Wrap *text* in Markdown italic for Telegram (via _render_text → MarkdownV2).

    Telegram has no native "dim" colour, so italic alone provides visual distinction
    from the final response text.
    """
    return f"*{text}*"


def build_streaming_callbacks(  # noqa: C901 PLR0915 — DEBT:wiring-bootstrap-deps — one closure per platform op
    adapter: "TelegramAdapter",
    original_msg: InboundMessage,
    outbound: OutboundMessage | None,
) -> "PlatformCallbacks":
    """Build PlatformCallbacks for StreamingSession from a TelegramAdapter context.

    Extracted from TelegramAdapter._make_streaming_callbacks to keep telegram.py
    under the 300-line file-length limit.
    """
    from lyra.adapters.shared._shared_streaming import PlatformCallbacks

    meta = _validate_inbound(original_msg, "send_streaming")
    if meta is None:

        async def _noop_placeholder() -> tuple[None, None]:
            raise ValueError("invalid inbound message")

        async def _noop_fallback(text: str) -> None:
            return None

        async def _noop_trace() -> tuple[None, None]:
            raise ValueError("invalid inbound message")

        return PlatformCallbacks(
            send_placeholder=_noop_placeholder,
            edit_placeholder_text=lambda ph, text: asyncio.sleep(0),
            send_trace_placeholder=_noop_trace,
            edit_trace=lambda ph, ev: asyncio.sleep(0),
            send_message=_noop_fallback,
            send_fallback=_noop_fallback,
            chunk_text=lambda text: [text],
            start_typing=lambda: None,
            cancel_typing=lambda: None,
            get_msg=lambda key, fallback: fallback,
            placeholder_text="\u2026",
        )

    chat_id, _, _ = meta
    _pm = original_msg.platform_meta
    reply_to: int | None = _pm.message_id if isinstance(_pm, TelegramMeta) else None
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
            except TelegramAPIError as exc:
                log.debug("Placeholder text edit skipped: %s", exc)

    async def _send_trace_placeholder() -> tuple[Any, int | None]:
        msg = await adapter.bot.send_message(
            chat_id=chat_id,
            text="🔧 …",
            **({"reply_to_message_id": reply_to} if reply_to is not None else {}),
        )
        return msg, msg.message_id

    async def _edit_trace(trace_obj: Any, event: Any) -> None:
        # v1 ToolSummaryRenderEvent removed in Slice 5 (#1192).
        # edit_trace is a no-op; trace placeholder used only for reasoning.
        pass

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

    # Mutable cells for _render_reasoning closure state (one per streaming turn).
    _reasoning_trace_cell: list[Any] = [None]
    _reasoning_accum_cell: list[str] = [""]
    _last_reasoning_edit_cell: list[float | None] = [None]

    async def _edit_trace_with_text(trace_obj: Any, text: str) -> None:
        """Edit the trace placeholder with plain/formatted text (for reasoning)."""
        rendered = _render_text(text)
        if rendered:
            try:
                await adapter.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=trace_obj.message_id,
                    text=rendered[0],
                    parse_mode="MarkdownV2",
                )
            except TelegramAPIError as exc:
                log.debug("Reasoning trace edit skipped: %s", exc)

    async def _render_reasoning(  # noqa: C901 — DEBT:wiring-bootstrap-deps — three-branch state machine
        trace_obj: Any,
        event: ReasoningStartRenderEvent
        | ReasoningDeltaRenderEvent
        | ReasoningEndRenderEvent,
    ) -> None:
        """Render reasoning events as dim italic text in the trace placeholder.

        Slice 4 (#1101): the `show_intermediate=False` gate lives upstream on
        `StreamProcessor` (SC-6) — when disabled, no Reasoning* events reach
        this callback. Single source of truth, no adapter-side double-gate.
        Delta edits are throttled by STREAMING_EDIT_INTERVAL.
        Accumulated text is truncated to 120 chars with '…' suffix.
        """
        if isinstance(event, ReasoningStartRenderEvent):
            effective_trace = (
                trace_obj if trace_obj is not None else _reasoning_trace_cell[0]
            )
            if effective_trace is None:
                try:
                    effective_trace, _ = await _send_trace_placeholder()
                    _reasoning_trace_cell[0] = effective_trace
                except Exception:
                    log.exception(
                        "Failed to send trace placeholder — reasoning will not render"
                    )
                    return
            _reasoning_accum_cell[0] = ""
            _last_reasoning_edit_cell[0] = None

        elif isinstance(event, ReasoningDeltaRenderEvent):
            effective_trace = (
                trace_obj if trace_obj is not None else _reasoning_trace_cell[0]
            )
            if effective_trace is None:
                return
            _reasoning_accum_cell[0] += event.delta
            truncated = _reasoning_accum_cell[0]
            if len(truncated) > 120:
                truncated = truncated[:117] + "…"
            now = time.monotonic()
            if (
                _last_reasoning_edit_cell[0] is None
                or (now - _last_reasoning_edit_cell[0]) >= STREAMING_EDIT_INTERVAL
            ):
                await _edit_trace_with_text(effective_trace, _dim_italic(truncated))
                _last_reasoning_edit_cell[0] = now

        else:  # ReasoningEndRenderEvent
            effective_trace = (
                trace_obj if trace_obj is not None else _reasoning_trace_cell[0]
            )
            if effective_trace is None:
                return
            # Final flush: guarantee last edit even if throttled
            if _reasoning_accum_cell[0]:
                truncated = _reasoning_accum_cell[0]
                if len(truncated) > 120:
                    truncated = truncated[:117] + "…"
                await _edit_trace_with_text(effective_trace, _dim_italic(truncated))

    return PlatformCallbacks(
        send_placeholder=_send_placeholder,
        edit_placeholder_text=_edit_placeholder_text,
        send_trace_placeholder=_send_trace_placeholder,
        edit_trace=_edit_trace,
        send_message=_send_message,
        send_fallback=_send_fallback,
        chunk_text=lambda text: _render_text(text) or [text],
        start_typing=lambda: adapter._start_typing(chat_id),
        cancel_typing=lambda: adapter._cancel_typing(chat_id),
        get_msg=adapter._msg,
        placeholder_text=_placeholder_text,
        edit_reasoning=_render_reasoning,
    )
