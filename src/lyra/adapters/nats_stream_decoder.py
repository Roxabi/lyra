"""Shared helpers for NATS streaming on the adapter side.

Owns:
- ``decode_stream_events``: async generator that decodes streamed chunks into
  render events, enforcing sequence ordering and bounded timeout.
- ``handle_stream_error`` / ``remember_terminated``: stream_error envelope
  handling (poison-pill dispatch + tombstone tracking).

Extracted from :class:`NatsOutboundListener` so the listener stays under the
repo-wide 300-line cap and so chunk-level protocol details are isolated from
the subscription lifecycle.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from lyra.core.hub.hub_protocol import RenderEvent

log = logging.getLogger(__name__)

_CHUNK_TIMEOUT_SECONDS = 120.0
_MAX_TERMINATED_STREAMS = 500


async def decode_stream_events(
    stream_id: str,
    q: asyncio.Queue[dict],
    *,
    counter: dict[str, int] | None = None,
) -> AsyncIterator["RenderEvent"]:
    """Drain chunks from *q* and yield decoded :class:`RenderEvent` objects.

    Enforces an in-order sequence check (warns on gaps) and bails out on a
    bounded per-chunk timeout so a stalled stream cannot park the drain task
    forever.

    Args:
        stream_id: Logical stream identifier (used only for log correlation).
        q:         Queue populated by :meth:`NatsOutboundListener._handle_chunk`.
        counter:   Caller-owned mutable dict passed through to
                   :meth:`NatsRenderEventCodec.decode` for version-mismatch
                   drop counting.  ``None`` skips counting.

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
        # stream_error is a transport-layer sentinel, not a render event —
        # terminate the stream without passing it through the codec.
        if event_type == "stream_error":
            break
        payload = chunk.get("payload", {})
        is_done = chunk.get("done", False)
        event = NatsRenderEventCodec.decode(event_type, payload, counter=counter)
        if event is not None:
            yield event
        if NatsRenderEventCodec.is_terminal(event_type, is_done):
            break


def remember_terminated(listener: Any, stream_id: str) -> None:
    """Record a terminated stream_id on the listener, bounding the set."""
    if len(listener._terminated_streams) >= _MAX_TERMINATED_STREAMS:
        listener._terminated_streams.pop()
    listener._terminated_streams.add(stream_id)


def handle_stream_error(listener: Any, data: dict) -> None:
    """Handle stream_error envelope — terminate or clean up the stream.

    Security: stream_error envelopes are trusted at the NATS auth layer
    (per-subject publish permissions). Defense-in-depth: only act on
    stream_ids we have local state for, matching ``_handle_send`` /
    ``_handle_attachment``'s pattern. Forged envelopes with unknown
    stream_ids are logged and dropped without touching any state,
    preventing tombstone-set pollution via random IDs.
    """
    stream_id = data.get("stream_id")
    if stream_id is None:
        return

    q = listener._stream_queues.get(stream_id)
    if q is not None:
        # Active stream — enqueue poison pill to terminate the drain loop.
        try:
            q.put_nowait({"event_type": "stream_error", "done": True})
        except asyncio.QueueFull:
            log.warning(
                "NatsOutboundListener: stream queue full, cannot enqueue"
                " stream_error for stream_id=%r",
                stream_id,
            )
        remember_terminated(listener, stream_id)
        return

    # No queue. Only act if we actually have state for this stream_id —
    # mirrors `_handle_send`'s unknown-stream_id handling and bounds the
    # blast radius of forged stream_error envelopes.
    known = (
        stream_id in listener._cache
        or stream_id in listener._stream_outbound
        or stream_id in listener._stream_tasks
    )
    if not known:
        log.warning(
            "NatsOutboundListener: stream_error for unknown stream_id=%r"
            " — no state to clean up",
            stream_id,
        )
        return

    # Legitimate race: error before first chunk, or after stream_end
    # already cleaned up. Record tombstone first so any late chunks that
    # beat the cache pop are rejected.
    remember_terminated(listener, stream_id)
    listener._cache.pop(stream_id, None)
    listener._cache_ts.pop(stream_id, None)
    listener._stream_outbound.pop(stream_id, None)
    # Symmetry with _drain_stream's finally block — ensure no stale entries.
    listener._stream_tasks.pop(stream_id, None)
    listener._stream_queues.pop(stream_id, None)
    log.warning(
        "NatsOutboundListener: stream_error for finished stream_id=%r",
        stream_id,
    )
