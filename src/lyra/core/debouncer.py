"""MessageDebouncer — hybrid typing aggregation for hub sessions (issue #145).

Prevents triggering the LLM on every partial message when users type in bursts.
Two strategies combined:
  1. Short debounce window: rapid messages (< debounce_ms apart) aggregated
     into a single InboundMessage with newline-joined text.
  2. Cancel-in-flight: if a new message arrives while the LLM is processing,
     cancel the in-flight task and re-dispatch with combined context.

Lifecycle:
  - One MessageDebouncer per Pool (per conversation scope).
  - Created by Pool.__init__(); debounce_ms tunable via !config (#135).
  - collect() and merge() are the public API used by Pool._process_loop().
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .message import InboundMessage

log = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_MS = 300


class MessageDebouncer:
    """Aggregates rapid-fire messages and supports cancel-in-flight.

    Usage inside Pool._process_loop()::

        msgs = await debouncer.collect(inbox)
        combined = MessageDebouncer.merge(msgs)
        # ... dispatch combined to agent
    """

    __slots__ = ("debounce_ms",)

    def __init__(self, debounce_ms: int = DEFAULT_DEBOUNCE_MS) -> None:
        self.debounce_ms = debounce_ms

    async def collect(
        self, inbox: asyncio.Queue[InboundMessage]
    ) -> list[InboundMessage]:
        """Wait for a message, then drain rapid follow-ups within the debounce window.

        Blocks until at least one message is available. After the first message,
        keeps draining from *inbox* as long as new messages arrive within
        *debounce_ms*. Returns all collected messages in arrival order.

        When debounce_ms <= 0, returns immediately after draining whatever is
        already queued (no wait), preserving pre-debounce behaviour.
        """
        first = await inbox.get()
        buffer: list[InboundMessage] = [first]

        if self.debounce_ms <= 0:
            # No debounce — drain what's already queued (non-blocking).
            while True:
                try:
                    buffer.append(inbox.get_nowait())
                except asyncio.QueueEmpty:
                    break
            return buffer

        timeout = self.debounce_ms / 1000.0
        try:
            while True:
                msg = await asyncio.wait_for(inbox.get(), timeout=timeout)
                buffer.append(msg)
        except asyncio.TimeoutError:
            pass

        if len(buffer) > 1:
            log.debug(
                "debounce: aggregated %d messages within %dms window",
                len(buffer),
                self.debounce_ms,
            )

        return buffer

    async def drain_followups(
        self, inbox: asyncio.Queue[InboundMessage]
    ) -> list[InboundMessage]:
        """Drain rapid follow-up messages within the debounce window.

        Unlike collect(), does NOT block for the first message — only drains
        what arrives within debounce_ms. Returns an empty list if nothing
        arrives in time or if debounce_ms <= 0.
        """
        buffer: list[InboundMessage] = []
        if self.debounce_ms <= 0:
            while True:
                try:
                    buffer.append(inbox.get_nowait())
                except asyncio.QueueEmpty:
                    break
            return buffer

        timeout = self.debounce_ms / 1000.0
        try:
            while True:
                msg = await asyncio.wait_for(inbox.get(), timeout=timeout)
                buffer.append(msg)
        except asyncio.TimeoutError:
            pass
        return buffer

    @staticmethod
    def merge(messages: list[InboundMessage]) -> InboundMessage:
        """Combine multiple messages into a single InboundMessage.

        Text fields are joined with newlines. Attachments are concatenated.
        is_mention is True if any constituent message was a mention.
        Metadata (id, platform_meta, routing, timestamp) comes from the
        most recent message.

        Returns the message unchanged when len(messages) == 1.
        Raises ValueError if messages is empty.
        """
        if not messages:
            raise ValueError("messages must be non-empty")
        if len(messages) == 1:
            return messages[0]

        combined_text = "\n".join(m.text for m in messages)
        combined_raw = "\n".join(m.text_raw for m in messages)
        merged_attachments = [a for m in messages for a in m.attachments]
        last = messages[-1]

        return replace(
            last,
            text=combined_text,
            text_raw=combined_raw,
            is_mention=any(m.is_mention for m in messages),
            attachments=merged_attachments,
        )
