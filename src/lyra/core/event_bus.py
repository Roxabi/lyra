"""EventBus — fan-out monitoring event bus with deduplication aggregator.

EventBus distributes MonitoringEvent instances to all active subscribers via
per-subscriber asyncio.Queue. Emitting never blocks: put_nowait() drops events
and increments dropped_events when a subscriber queue is full.

EventAggregator subscribes to the bus and deduplicates state transitions so
that downstream actions (e.g. alerting) fire only on actual state changes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncGenerator

from .events import (
    AgentCompleted,
    AgentFailed,
    AgentIdle,
    AgentStarted,
    CircuitStateChanged,
    MonitoringEvent,
    QueueDepthExceeded,
    QueueDepthNormal,
)

log = logging.getLogger(__name__)

MAX_SIZE = 1000


class EventBus:
    """Fan-out monitoring event bus.

    Subscribers receive a copy of every emitted event via their own bounded
    asyncio.Queue. Events are dropped (and counted) when a subscriber queue is
    full — emit() never blocks.

    Usage::

        bus = EventBus()

        # Producer
        bus.emit(AgentStarted(agent_id="lyra"))

        # Consumer
        async for event in bus.subscribe():
            handle(event)
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[MonitoringEvent]] = []
        self.dropped_events: int = 0
        self.dropped_events_since: float = time.monotonic()

    def emit(self, event: MonitoringEvent) -> None:
        """Emit an event to all subscribers.

        Uses put_nowait() — never blocks. Increments dropped_events if a
        subscriber queue is at capacity.
        """
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped_events += 1
                log.warning(
                    "EventBus: subscriber queue full, event dropped (total dropped=%d)",
                    self.dropped_events,
                )

    async def subscribe(self) -> AsyncGenerator[MonitoringEvent, None]:
        """Async generator that yields events as they arrive.

        Registers a new bounded queue for this subscriber. The queue is
        removed when the generator is closed or cancelled.
        """
        queue: asyncio.Queue[MonitoringEvent] = asyncio.Queue(maxsize=MAX_SIZE)
        self._subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            try:
                self._subscribers.remove(queue)
            except ValueError:
                pass

    def reset_dropped_events(self) -> tuple[int, float]:
        """Return and reset the dropped events counter.

        Returns:
            Tuple of (count, since) where count is the number of dropped events
            since the last reset and since is the monotonic timestamp of the
            previous reset.
        """
        count, since = self.dropped_events, self.dropped_events_since
        self.dropped_events = 0
        self.dropped_events_since = time.monotonic()
        return count, since


class EventAggregator:
    """Deduplicating aggregator over an EventBus.

    Maintains a state dict keyed by composite strings (e.g. ``"pool:agent_id"``)
    and calls ``_trigger_monitoring_action`` only when the state actually
    changes. Prevents duplicate alerts for persistent failures.

    Usage::

        aggregator = EventAggregator(bus)
        task = asyncio.create_task(aggregator.run())
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._state: dict[str, str] = {}

    async def run(self) -> None:
        """Subscribe to the bus and process events until cancelled."""
        try:
            async for event in self._bus.subscribe():
                try:
                    await self._handle(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception(
                        "EventAggregator: unhandled error processing event %r", event
                    )
        except asyncio.CancelledError:
            log.debug("EventAggregator: run() cancelled, shutting down")

    def _state_key(self, event: MonitoringEvent) -> str:
        """Return the composite state key for the given event."""
        if isinstance(event, (AgentFailed, AgentCompleted, AgentIdle, AgentStarted)):
            return f"pool:{event.agent_id}"
        if isinstance(event, CircuitStateChanged):
            return f"circuit:{event.platform}"
        if isinstance(event, (QueueDepthExceeded, QueueDepthNormal)):
            return f"queue:{event.queue_name}"
        return f"unknown:{type(event).__name__}"

    def _changed(self, key: str, new_state: str) -> bool:
        """Return True if the state for key has changed to new_state.

        Updates _state[key] when a change is detected.
        """
        if self._state.get(key) == new_state:
            return False
        self._state[key] = new_state
        return True

    async def _handle(self, event: MonitoringEvent) -> None:
        """Apply deduplication logic and trigger action on state change."""
        if isinstance(event, AgentFailed):
            key = self._state_key(event)
            new_state = "failed"
        elif isinstance(event, (AgentCompleted, AgentIdle)):
            key = self._state_key(event)
            new_state = "healthy"
        elif isinstance(event, AgentStarted):
            key = self._state_key(event)
            new_state = "running"
        elif isinstance(event, CircuitStateChanged):
            key = self._state_key(event)
            new_state = event.new_state
        elif isinstance(event, QueueDepthExceeded):
            key = self._state_key(event)
            new_state = "exceeded"
        elif isinstance(event, QueueDepthNormal):
            key = self._state_key(event)
            new_state = "normal"
        else:
            return

        if not self._changed(key, new_state):
            return

        await self._trigger_monitoring_action(event)

    async def _trigger_monitoring_action(self, event: MonitoringEvent) -> None:
        """Trigger a monitoring action for a confirmed state change.

        Currently logs a warning. Future: LLM alert, PagerDuty, etc.
        """
        log.warning("EventAggregator: state change detected — %r", event)


# ── Module-level singleton ────────────────────────────────────────────────────

_bus: EventBus | None = None


def get_event_bus() -> EventBus | None:
    """Return the module-level EventBus singleton, or None if not set."""
    return _bus


def set_event_bus(bus: EventBus | None) -> None:
    """Replace the module-level EventBus singleton (for testing)."""
    global _bus
    _bus = bus
