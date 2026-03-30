"""Fire-and-forget fan-out bus for pipeline telemetry events (#432).

Subscribers receive events via per-subscriber ``asyncio.Queue`` instances.
The pipeline is never blocked by a slow consumer — ``put_nowait`` drops
events on ``QueueFull`` with a rate-limited warning.

Injected via constructor (not a singleton) per ADR-025 F-10.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .pipeline_events import PipelineEvent

log = logging.getLogger(__name__)

_DROP_WARN_INTERVAL = 60.0


class PipelineEventBus:
    """Fan-out bus for pipeline telemetry events.

    Usage::

        bus = PipelineEventBus(maxsize=1000)
        queue = bus.subscribe()          # consumer gets a Queue
        bus.emit(MessageReceived(...))   # fans out to all subscribers
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._subscribers: list[asyncio.Queue[PipelineEvent]] = []
        self._last_warn: dict[int, float] = {}

    def subscribe(self) -> asyncio.Queue[PipelineEvent]:
        """Create and return a new subscriber queue."""
        q: asyncio.Queue[PipelineEvent] = asyncio.Queue(
            maxsize=self._maxsize,
        )
        self._subscribers.append(q)
        return q

    def emit(self, event: PipelineEvent) -> None:
        """Fan out *event* to all subscriber queues.

        Never blocks. Drops the event for any subscriber whose queue is
        full, logging a rate-limited warning (max once per 60s per subscriber).
        """
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._warn_drop(q)

    def _warn_drop(self, q: asyncio.Queue[PipelineEvent]) -> None:
        """Log a rate-limited warning when events are dropped."""
        now = time.monotonic()
        qid = id(q)
        if now - self._last_warn.get(qid, 0.0) >= _DROP_WARN_INTERVAL:
            self._last_warn[qid] = now
            log.warning(
                "PipelineEventBus: subscriber queue full"
                " — events dropped (maxsize=%d)",
                self._maxsize,
            )
