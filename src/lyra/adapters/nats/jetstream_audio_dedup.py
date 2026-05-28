"""Dedup set implementations for JetStreamAudioConsumer.

Two implementations sharing the same async interface:

``InMemorySentSet`` (V1 / fallback)
    OrderedDict-backed TTL set with FIFO eviction cap.
    Does NOT survive a process restart — use only for testing or as fallback.

``KvSentSet`` (V2 / production)
    JetStream KV-backed set. Survives restarts. Best-effort dedup: check-then-
    act is not atomic (separate get + put, no CAS / put-if-absent), so
    exactly-once across concurrent replicas is not guaranteed. Current single-
    process deployment is unaffected. Uses the ``lyra_outbound_audio_sent``
    bucket provisioned by ``ensure_kv`` (TTL=900s).

Interface contract (both impls must satisfy)::

    async def already_sent(self, stream_id: str) -> bool
    async def mark_sent(self, stream_id: str) -> None

KV key encoding
---------------
NATS KV keys allow ``[-/_=.a-zA-Z0-9]``. Stream IDs are opaque message IDs
that may contain characters outside that set (colons, angle brackets, etc.).
Both methods encode ``stream_id`` to hex (UTF-8 bytes → lowercase hex string)
before using it as a KV key.  The encoding is injective and only produces
``[0-9a-f]``, so it is always a valid KV key.

T10 wiring
----------
Bootstrap calls ``kv = await ensure_kv(js)`` and passes ``dedup=KvSentSet(kv)``
to ``JetStreamAudioConsumer``.  No other change is needed in the consumer.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Protocol, runtime_checkable

from nats.js.errors import KeyNotFoundError


@runtime_checkable
class _KvLike(Protocol):
    """Structural protocol for the KV operations KvSentSet actually uses.

    Both the real ``nats.js.kv.KeyValue`` and the test ``FakeKv`` satisfy
    this protocol structurally — no inheritance required.
    """

    async def get(self, key: str) -> Any: ...
    async def put(self, key: str, value: bytes) -> Any: ...


# TTL: ack_wait × max_deliver × 2 headroom = 90 × 5 × 2 = 900 s.
# Must be ≥ ack_wait × max_deliver = 450 s (floor).
DEDUP_TTL = 900.0  # seconds

# FIFO eviction cap — bounds memory use under high-volume bursts.
DEDUP_MAX_ENTRIES = 1_000


def _encode_key(stream_id: str) -> str:
    """Encode stream_id to a valid NATS KV key (hex of UTF-8 bytes).

    KV keys allow ``[-/_=.a-zA-Z0-9]``.  Stream IDs are opaque and may
    contain colons, spaces, or other forbidden characters.  Hex encoding is
    injective, produces only ``[0-9a-f]``, and is reversible.
    """
    return stream_id.encode("utf-8").hex()


class InMemorySentSet:
    """Async in-memory dedup set backed by an OrderedDict with TTL + FIFO eviction.

    Thread safety: NOT thread-safe (asyncio single-threaded use only).
    Does NOT survive process restart — use KvSentSet in production.
    """

    def __init__(
        self,
        ttl: float = DEDUP_TTL,
        max_entries: int = DEDUP_MAX_ENTRIES,
    ) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._sent: OrderedDict[str, float] = OrderedDict()

    async def already_sent(self, stream_id: str) -> bool:
        """Return True if stream_id was marked sent within the TTL window.

        Expired entries are evicted lazily on access.  Returns False for
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

    async def mark_sent(self, stream_id: str) -> None:
        """Record stream_id as successfully sent.

        Moves an existing entry to the end (refresh) and evicts the oldest
        entries when the cap is reached.
        """
        self._sent[stream_id] = time.monotonic()
        self._sent.move_to_end(stream_id)
        while len(self._sent) > self._max:
            self._sent.popitem(last=False)


class KvSentSet:
    """Async KV-backed dedup set using JetStream KeyValue.

    Survives process restarts. Best-effort dedup only: check (get) and act
    (put) are separate operations — no atomic CAS. Exactly-once across
    concurrent replicas is not guaranteed; single-process deployment is
    unaffected. Expiry is handled by the bucket TTL (900s), not by this class.

    KV key = hex(stream_id.encode('utf-8')) — always a valid NATS KV key
    regardless of what characters appear in the raw stream_id.
    """

    def __init__(self, kv: _KvLike) -> None:
        self._kv = kv

    async def already_sent(self, stream_id: str) -> bool:
        """Return True if stream_id has a live entry in the KV bucket.

        ``KeyNotFoundError`` (key absent or expired) → False (not sent).
        Any other error propagates so the caller can decide not to ack.
        """
        key = _encode_key(stream_id)
        try:
            await self._kv.get(key)
            return True
        except KeyNotFoundError:
            return False

    async def mark_sent(self, stream_id: str) -> None:
        """Write stream_id into the KV bucket.

        Uses ``kv.put`` which creates or updates the key.  The bucket TTL
        (900s, set during ensure_kv provisioning) handles expiry automatically.
        """
        key = _encode_key(stream_id)
        await self._kv.put(key, b"1")
