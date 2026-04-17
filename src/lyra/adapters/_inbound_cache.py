"""TTL-bounded cache for inbound messages (NATS outbound correlation).

The adapter caches each ``InboundMessage`` so that outbound envelopes
arriving from the hub via NATS can be matched to the original message.
On cache miss (TTL expiry, restart, eviction) the cache falls back to
reconstructing the message from an embedded ``original_msg`` dict in the
envelope — this prevents silent message drops.
"""

from __future__ import annotations

import asyncio
import logging
import time

from lyra.core.message import InboundMessage
from lyra.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_nats._serialize import _TypeHintResolver, deserialize_dict

log = logging.getLogger(__name__)

MAX_SIZE = 500
# 10 min — covers LLM response latency + NATS reconnect window; see #622
TTL_SECONDS = 600
REAPER_INTERVAL_SECONDS = 30


class InboundCache:
    """stream_id → InboundMessage cache with TTL reaper."""

    __slots__ = ("_msgs", "_resolver", "_ts")

    def __init__(self, *, resolver: _TypeHintResolver = TYPE_REGISTRY_RESOLVER) -> None:
        self._msgs: dict[str, InboundMessage] = {}
        self._ts: dict[str, float] = {}
        self._resolver = resolver

    # -- public API --------------------------------------------------

    def put(self, msg: InboundMessage) -> None:
        """Store *msg* keyed by ``msg.id``."""
        if len(self._msgs) >= MAX_SIZE:
            oldest = next(iter(self._msgs))
            self._msgs.pop(oldest)
            self._ts.pop(oldest, None)
            log.warning(
                "InboundCache full (%d), evicted %r",
                MAX_SIZE,
                oldest,
            )
        self._msgs[msg.id] = msg
        self._ts[msg.id] = time.monotonic()

    def get(self, stream_id: str | None) -> InboundMessage | None:
        """Return cached msg or ``None``."""
        if stream_id is None:
            return None
        return self._msgs.get(stream_id)

    def touch(self, stream_id: str) -> None:
        """Refresh TTL for *stream_id* (e.g. while streaming)."""
        if stream_id in self._ts:
            self._ts[stream_id] = time.monotonic()

    def pop(self, stream_id: str) -> None:
        """Remove *stream_id* from cache (delivery complete)."""
        self._msgs.pop(stream_id, None)
        self._ts.pop(stream_id, None)

    def resolve(
        self,
        data: dict,
        kind: str,
    ) -> tuple[str, InboundMessage] | None:
        """Look up original msg for an outbound envelope.

        1. Try cache by ``stream_id``.
        2. On miss, reconstruct from ``original_msg`` in the envelope.
        3. Return ``None`` (with warning) if both fail.
        """
        stream_id: str | None = data.get("stream_id")
        if stream_id is None:
            log.warning(
                "InboundCache: missing stream_id in %s envelope",
                kind,
            )
            return None
        msg = self._msgs.get(stream_id)
        if msg is None:
            raw = data.get("original_msg")
            if raw is not None:
                try:
                    msg = deserialize_dict(raw, InboundMessage, resolver=self._resolver)
                except Exception:
                    log.warning(
                        "InboundCache: bad embedded original_msg for %s stream_id=%r",
                        kind,
                        stream_id,
                    )
            if msg is None:
                log.warning(
                    "InboundCache: unknown stream_id=%r for %s",
                    stream_id,
                    kind,
                )
                return None
        return stream_id, msg

    # -- internal ----------------------------------------------------

    def _reap(self) -> list[str]:
        """Evict entries past TTL.  Returns evicted stream_ids."""
        now = time.monotonic()
        stale = [sid for sid, ts in list(self._ts.items()) if now - ts > TTL_SECONDS]
        for sid in stale:
            log.warning(
                "InboundCache: evicting stale stream_id=%r",
                sid,
            )
            self._msgs.pop(sid, None)
            self._ts.pop(sid, None)
        return stale

    def __contains__(self, stream_id: str) -> bool:
        return stream_id in self._msgs


async def run_reaper(cache: InboundCache) -> None:
    """Periodically call ``cache._reap()``.  Runs until cancelled."""
    while True:
        await asyncio.sleep(REAPER_INTERVAL_SECONDS)
        cache._reap()
