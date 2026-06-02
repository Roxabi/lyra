"""Structured JSON audit logger for pipeline telemetry (#432).

First consumer of ``PipelineEventBus``. Drains a subscriber queue and
logs every event as structured JSON via stdlib logging. Best-effort:
on shutdown (task cancellation), remaining queued events are dropped.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging

from .pipeline_events import PipelineEvent

log = logging.getLogger(__name__)


class AuditConsumer:
    """Logs pipeline events as structured JSON for post-hoc debugging.

    Usage::

        queue = bus.subscribe()
        consumer = AuditConsumer(queue)
        task = asyncio.create_task(consumer.run())
    """

    def __init__(self, queue: asyncio.Queue[PipelineEvent]) -> None:
        self._queue = queue

    async def run(self) -> None:
        """Drain the queue and log each event. Runs until cancelled."""
        while True:
            event = await self._queue.get()
            log.info(
                "pipeline.%s",
                event.stage,
                extra={"event": dataclasses.asdict(event)},
            )
            self._queue.task_done()
