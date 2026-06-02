"""platform_send — shared send loop for non-streaming outbound messages.

Root cause (P5 audit): telegram_outbound.send() and discord_outbound.send()
contain structurally identical for-loops:
  - iterate chunks
  - apply reply-to on chunk 0
  - apply buttons/view on the last chunk
  - store reply_message_id in outbound.metadata on the last chunk
  - call _start_typing or _cancel_typing based on outbound.intermediate

The only platform differences are:
  1. How to send a single chunk (bot.send_message vs messageable.send/reply).
  2. How to extract the sent message id (sent.message_id vs sent.id).

Correction class: Archi — the loop is extracted here; platforms inject a
ChunkSender callable that handles the platform-specific send mechanic.
Level L = shared adapter layer (¬symptom layer = individual send functions).

Usage (in telegram_outbound.send / discord_outbound.send after migration):

    async def send_chunk(chunk: str, is_first: bool, is_last: bool,
                         buttons: Any) -> int:
        ...  # platform-specific send; return sent message id

    ctx = SendContext(
        chunks=chunks,
        buttons=render_buttons(outbound.buttons),
        outbound=outbound,
        adapter=adapter,
        scope_id=chat_id,  # or send_to_id
    )
    await send_chunked_message(ctx, send_chunk)

Note: Tasks 2 and 3 wire the per-platform send() implementations to call this
helper.  This module is additive — it does NOT modify telegram or discord files.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory.adapters.shared._base_outbound import OutboundAdapterBase
    from factory.core.messaging.message import OutboundMessage

log = logging.getLogger(__name__)

__all__ = ["ChunkSender", "SendContext", "send_chunked_message"]

# A callable that sends a single chunk and returns the sent message id.
# Positional args (in order):
#   chunk      — text payload (already escaped/chunked by the platform formatter)
#   is_first   — True when this is the first chunk in the sequence
#   is_last    — True when this is the last chunk in the sequence
#   buttons    — platform-native button object (e.g. InlineKeyboardMarkup / View),
#                None when there are no buttons or this is not the last chunk
# Returns the platform message id (int) for the sent message.
ChunkSender = Callable[
    [str, bool, bool, Any],
    Coroutine[Any, Any, int],
]


@dataclass
class SendContext:
    """Grouped parameters for send_chunked_message.

    Attributes
    ----------
    chunks:
        Pre-rendered, platform-legal text chunks produced by the formatter.
        An empty list is a no-op for the send loop; typing still runs.
    buttons:
        Platform-native button object rendered by the formatter, or None.
        Forwarded to *send_chunk* only on the last chunk.
    outbound:
        The OutboundMessage being delivered.  On success this function sets
        ``outbound.metadata["reply_message_id"]`` to the last sent message id.
    adapter:
        The platform adapter — supplies ``_start_typing`` / ``_cancel_typing``.
    scope_id:
        Numeric scope (chat_id / channel_id / thread_id) for the typing
        indicator.
    """

    chunks: list[str]
    buttons: Any
    outbound: "OutboundMessage"
    adapter: "OutboundAdapterBase"
    scope_id: int


async def send_chunked_message(ctx: SendContext, send_chunk: ChunkSender) -> None:
    """Send ``ctx.chunks`` sequentially, then manage the typing indicator.

    Parameters
    ----------
    ctx:
        Grouped send parameters — see ``SendContext``.
    send_chunk:
        Platform-specific callable — see ``ChunkSender`` alias above.
        Receives ``(chunk, is_first, is_last, buttons_or_None)`` and must
        return the platform message id (int) of the sent message.

    Notes
    -----
    - ``reply_message_id`` is written into ``ctx.outbound.metadata`` only when
      at least one chunk is successfully sent.
    - Typing start/cancel always runs after the send loop, even on empty input.
    - Exceptions from ``send_chunk`` propagate to the caller; no retry logic
      is applied here (retry belongs in the platform-specific implementation).
    """
    last_idx = len(ctx.chunks) - 1
    for i, chunk in enumerate(ctx.chunks):
        is_first = i == 0
        is_last = i == last_idx
        chunk_buttons = ctx.buttons if (is_last and ctx.buttons is not None) else None
        sent_id = await send_chunk(chunk, is_first, is_last, chunk_buttons)
        if is_last:
            ctx.outbound.metadata["reply_message_id"] = sent_id

    if ctx.outbound.intermediate:
        ctx.adapter._start_typing(ctx.scope_id)
    else:
        ctx.adapter._cancel_typing(ctx.scope_id)
