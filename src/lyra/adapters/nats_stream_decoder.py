"""Shared async generator that decodes NATS streaming chunks into render events.

Extracted from :class:`NatsOutboundListener` so the streaming decode loop can
live in its own module (keeps the listener under the repo-wide 300-line cap
and isolates the chunk-level protocol details from the subscription lifecycle).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from lyra.core.hub.hub_protocol import RenderEvent

log = logging.getLogger(__name__)

_CHUNK_TIMEOUT_SECONDS = 120.0


async def decode_stream_events(
    stream_id: str, q: asyncio.Queue[dict],
) -> AsyncIterator["RenderEvent"]:
    """Drain chunks from *q* and yield decoded :class:`RenderEvent` objects.

    Enforces an in-order sequence check (warns on gaps) and bails out on a
    bounded per-chunk timeout so a stalled stream cannot park the drain task
    forever.

    Args:
        stream_id: Logical stream identifier (used only for log correlation).
        q: Queue populated by :meth:`NatsOutboundListener._handle_chunk`.

    Yields:
        Decoded render events until a terminal chunk arrives or the timeout
        elapses.
    """
    from lyra.nats.render_event_codec import NatsRenderEventCodec

    expected_seq = 0
    while True:
        try:
            chunk = await asyncio.wait_for(q.get(), timeout=_CHUNK_TIMEOUT_SECONDS)
        except TimeoutError:
            log.warning(
                "NatsOutboundListener: stream timed out waiting for chunk"
                " stream_id=%r (120s)",
                stream_id,
            )
            break
        seq = chunk.get("seq")
        if seq is not None and seq != expected_seq:
            log.warning(
                "NatsOutboundListener: out-of-order chunk"
                " stream_id=%r expected_seq=%d got_seq=%d",
                stream_id,
                expected_seq,
                seq,
            )
        expected_seq += 1
        event_type = chunk.get("event_type", "text")
        payload = chunk.get("payload", {})
        is_done = chunk.get("done", False)
        event = NatsRenderEventCodec.decode(event_type, payload)
        if event is not None:
            yield event
        if NatsRenderEventCodec.is_terminal(event_type, is_done):
            break
