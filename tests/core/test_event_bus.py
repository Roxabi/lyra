"""Tests for EventBus and EventAggregator (SC-2, SC-3, SC-4, SC-8, SC-9, SC-14)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import lyra.core.event_bus as event_bus_module
from lyra.core.event_bus import EventAggregator, EventBus, set_event_bus
from lyra.core.events import (
    AgentCompleted,
    AgentFailed,
    CircuitStateChanged,
    QueueDepthExceeded,
)


@pytest.fixture(autouse=True)
def reset_bus():
    """Reset the global event bus singleton before and after each test."""
    set_event_bus(None)
    yield
    set_event_bus(None)


# ---------------------------------------------------------------------------
# EventBus tests
# ---------------------------------------------------------------------------


class TestEventBusFanout:
    async def test_fanout_two_subscribers(self) -> None:
        """SC-2: emit one event, both subscribers receive it."""
        bus = EventBus()
        event = AgentFailed(agent_id="abc", error="boom")

        async def collect(gen, results: list) -> None:
            async for item in gen:
                results.append(item)
                return  # collect one item then stop

        results_a: list = []
        results_b: list = []

        gen_a = bus.subscribe()
        gen_b = bus.subscribe()

        task_a = asyncio.create_task(collect(gen_a, results_a))
        task_b = asyncio.create_task(collect(gen_b, results_b))

        # Give subscribers time to start waiting
        await asyncio.sleep(0)

        bus.emit(event)

        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2.0)

        assert results_a == [event]
        assert results_b == [event]

        await gen_a.aclose()
        await gen_b.aclose()


class TestEventBusDropCounter:
    async def test_drop_counter_on_full_queue(self) -> None:
        """SC-3: fill a subscriber queue, emit again, assert dropped_events == 1."""
        event = AgentFailed(agent_id="abc", error="boom")

        with patch.object(event_bus_module, "MAX_SIZE", 1):
            bus = EventBus()
            gen = bus.subscribe()

            # Advance the generator so it creates its queue and registers as subscriber
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0)

            assert len(bus._subscribers) == 1
            assert bus._subscribers[0].maxsize == 1

            # First emit fills the queue (maxsize=1)
            bus.emit(event)
            # Second emit should be dropped
            bus.emit(event)

            assert bus.dropped_events == 1

            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            await gen.aclose()


class TestEventBusNoRaise:
    def test_emit_never_raises_no_subscribers(self) -> None:
        """SC-4: emit with no subscribers registered must not raise."""
        bus = EventBus()
        event = AgentFailed(agent_id="abc", error="boom")
        # Must not raise — no subscribers
        bus.emit(event)

    async def test_emit_never_raises_full_queue(self) -> None:
        """SC-4: emit when subscriber queue is full must not raise."""
        event = AgentFailed(agent_id="abc", error="boom")

        with patch.object(event_bus_module, "MAX_SIZE", 1):
            bus = EventBus()
            gen = bus.subscribe()

            # Advance the generator so it creates its queue and registers as subscriber
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0)

            # Fill the queue (maxsize=1)
            bus.emit(event)
            # Queue is now full — must not raise
            bus.emit(event)

            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            await gen.aclose()


class TestEventBusCleanup:
    async def test_subscribe_cleanup(self) -> None:
        """SC-8: after generator closes, queue removed from _subscribers."""
        bus = EventBus()

        gen = bus.subscribe()

        # Advance the generator so it creates its queue and registers as subscriber
        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)

        # At least one subscriber registered
        assert len(bus._subscribers) == 1

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        await gen.aclose()

        # After close, subscriber queue must be removed
        assert len(bus._subscribers) == 0


# ---------------------------------------------------------------------------
# EventAggregator tests
# ---------------------------------------------------------------------------


class TestEventAggregatorChanged:
    def test_changed_returns_false_on_same_state(self) -> None:
        """SC-9: _changed("pool:x", "failed") twice — first True, second False."""
        agg = EventAggregator(bus=EventBus())
        assert agg._changed("pool:x", "failed") is True
        assert agg._changed("pool:x", "failed") is False

    def test_changed_returns_true_on_different_state(self) -> None:
        """SC-9: "failed" then "healthy" — both True."""
        agg = EventAggregator(bus=EventBus())
        assert agg._changed("pool:x", "failed") is True
        assert agg._changed("pool:x", "healthy") is True


class TestEventAggregatorStateKey:
    def test_state_key_agent_event(self) -> None:
        """SC-14: _state_key(AgentFailed(agent_id="abc")) == "pool:abc"."""
        agg = EventAggregator(bus=EventBus())
        event = AgentFailed(agent_id="abc", error="boom")
        assert agg._state_key(event) == "pool:abc"

    def test_state_key_circuit_event(self) -> None:
        """SC-14: _state_key(CircuitStateChanged(platform="tg")) == "circuit:tg"."""
        agg = EventAggregator(bus=EventBus())
        event = CircuitStateChanged(platform="tg", old_state="closed", new_state="open")
        assert agg._state_key(event) == "circuit:tg"

    def test_state_key_queue_event(self) -> None:
        """SC-14: QueueDepthExceeded(queue_name="staging") -> "queue:staging"."""
        agg = EventAggregator(bus=EventBus())
        event = QueueDepthExceeded(queue_name="staging", depth=10, threshold=5)
        assert agg._state_key(event) == "queue:staging"


class TestEventAggregatorDedup:
    async def test_aggregator_dedup_no_action_on_same_state(self) -> None:
        """SC-9: emit same AgentFailed twice — _trigger_monitoring_action once."""
        bus = EventBus()
        agg = EventAggregator(bus=bus)
        event = AgentFailed(agent_id="abc", error="boom")

        with patch.object(
            agg, "_trigger_monitoring_action", new_callable=AsyncMock
        ) as mock_action:
            await asyncio.wait_for(agg._handle(event), timeout=2.0)
            await asyncio.wait_for(agg._handle(event), timeout=2.0)

        mock_action.assert_called_once()

    async def test_aggregator_fires_on_state_change(self) -> None:
        """SC-9: emit AgentCompleted then AgentFailed — action called twice."""
        bus = EventBus()
        agg = EventAggregator(bus=bus)
        event_completed = AgentCompleted(agent_id="abc", duration_ms=42.0)
        event_failed = AgentFailed(agent_id="abc", error="boom")

        with patch.object(
            agg, "_trigger_monitoring_action", new_callable=AsyncMock
        ) as mock_action:
            await asyncio.wait_for(agg._handle(event_completed), timeout=2.0)
            await asyncio.wait_for(agg._handle(event_failed), timeout=2.0)

        assert mock_action.call_count == 2
