"""StateMachine[K, V] — per-consumer open-block and dedup tracking primitive.

Generic helper consumed by stream parsers (Phase 5 — #1282). Each parser
instantiates its own StateMachine; no cross-consumer key sharing.

State buckets:
  - open_blocks: dict[K, V]  — live block ID → block metadata
  - pending: deque[V]        — emissions accumulated for caller to drain
  - dedup_seen: set[K]       — IDs already emitted (anti-double-fire guard)

Type variance note: K is bound to Hashable (invariant — used as dict/set key).
V is invariant (stored in mutable containers: dict and deque). Both constraints
are intentional; covariant/contravariant bounds would require read-only
protocols that do not match the mutation semantics here.
"""

from __future__ import annotations

from collections import deque
from typing import Generic, Hashable, Iterable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class StateMachine(Generic[K, V]):
    """Per-consumer state-tracking primitives for stream parsers.

    Each parser instantiates its own StateMachine; no cross-consumer key
    sharing. Holds:
      - open_blocks: dict[K, V]  — open block ID → block metadata
      - pending: deque[V]        — accumulated emissions drained by caller
      - dedup_seen: set[K]       — IDs already emitted (anti-double-fire)
    """

    def __init__(self) -> None:
        self.open_blocks: dict[K, V] = {}
        self.pending: deque[V] = deque()
        self.dedup_seen: set[K] = set()

    def open(self, key: K, value: V) -> None:
        """Register a new open block under *key* with associated *value*."""
        self.open_blocks[key] = value

    def close(self, key: K) -> V | None:
        """Remove and return the value for *key*, or None if not open."""
        return self.open_blocks.pop(key, None)

    def is_open(self, key: K) -> bool:
        """Return True if *key* is currently in open_blocks."""
        return key in self.open_blocks

    def mark_seen(self, key: K) -> bool:
        """Add *key* to dedup_seen. Return True only on first addition."""
        if key in self.dedup_seen:
            return False
        self.dedup_seen.add(key)
        return True

    def drain(self) -> Iterable[V]:
        """Yield all pending items then clear the queue.

        Uses snapshot-then-clear semantics: a copy is taken before clearing
        so that mutations to ``self.pending`` during iteration are safe.
        Callers should consume the result immediately (e.g. ``list(sm.drain())``
        or ``yield from sm.drain()``).
        """
        snapshot = list(self.pending)
        self.pending.clear()
        yield from snapshot
