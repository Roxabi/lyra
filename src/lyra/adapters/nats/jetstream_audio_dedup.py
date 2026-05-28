"""In-memory dedup set for JetStreamAudioConsumer (V1).

V1 implementation: OrderedDict-backed TTL set with FIFO eviction cap.

T10 swap target
---------------
T10 replaces the injected ``InMemorySentSet`` instance with a ``KvSentSet``
(async, backed by the ``lyra_outbound_audio_sent`` JetStream KV bucket
provisioned by ``ensure_kv`` in ``infrastructure/outbound_audio/stream_setup.py``).

The consumer only calls ``already_sent(stream_id)`` and ``mark_sent(stream_id)``.
T10 makes those async and updates the call-sites in ``_process`` — the loop
logic is otherwise unchanged.

Interface contract (both V1 and future KvSentSet must satisfy):
    already_sent(stream_id: str) -> bool   (sync V1 / async KvSentSet)
    mark_sent(stream_id: str) -> None      (sync V1 / async KvSentSet)
"""

from __future__ import annotations

import time
from collections import OrderedDict

# TTL: ack_wait × max_deliver × 2 headroom = 90 × 5 × 2 = 900 s.
# Must be ≥ ack_wait × max_deliver = 450 s (floor).
DEDUP_TTL = 900.0  # seconds

# FIFO eviction cap — bounds memory use under high-volume bursts.
DEDUP_MAX_ENTRIES = 1_000


class InMemorySentSet:
    """Sync in-memory dedup set backed by an OrderedDict with TTL + FIFO eviction.

    Thread safety: NOT thread-safe (asyncio single-threaded use only).

    T10 swap:
        Replace the ``InMemorySentSet()`` instance injected into
        ``JetStreamAudioConsumer`` with a ``KvSentSet`` instance.
        ``KvSentSet.already_sent`` / ``mark_sent`` will be async; update
        the two call-sites in ``JetStreamAudioConsumer._process`` to
        ``await self._dedup.already_sent(sid)`` /
        ``await self._dedup.mark_sent(sid)``.
    """

    def __init__(
        self,
        ttl: float = DEDUP_TTL,
        max_entries: int = DEDUP_MAX_ENTRIES,
    ) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._sent: OrderedDict[str, float] = OrderedDict()

    def already_sent(self, stream_id: str) -> bool:
        """Return True if stream_id was marked sent within the TTL window.

        Expired entries are evicted lazily on access. Returns False for
        expired entries so a late redeliver past the TTL can proceed.
        """
        now = time.monotonic()
        ts = self._sent.get(stream_id)
        if ts is None:
            return False
        if now - ts > self._ttl:
            self._sent.pop(stream_id, None)
            return False
        return True

    def mark_sent(self, stream_id: str) -> None:
        """Record stream_id as successfully sent.

        Moves an existing entry to the end (refresh) and evicts the oldest
        entries when the cap is reached.
        """
        self._sent[stream_id] = time.monotonic()
        self._sent.move_to_end(stream_id)
        while len(self._sent) > self._max:
            self._sent.popitem(last=False)
