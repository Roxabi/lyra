"""Tests for EventAggregator dedup state machine."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from lyra.core.event_bus import EventAggregator, EventBus, set_event_bus
from lyra.core.events import (
    AgentCompleted,
    AgentFailed,
    CircuitStateChanged,
    QueueDepthExceeded,
    QueueDepthNormal,
)


@pytest.fixture(autouse=True)
def reset_bus():
    set_event_bus(None)
    yield
    set_event_bus(None)


class TestEventAggregatorStateKey:
    def test_state_key_agent_event(self):
        agg = EventAggregator(bus=EventBus())
        key = agg._state_key(AgentFailed(agent_id="lyra", pool_id="tg:bot:123"))
        assert key == "pool:tg:bot:123"

    def test_state_key_circuit_event(self):
        agg = EventAggregator(bus=EventBus())
        assert agg._state_key(CircuitStateChanged(platform="tg")) == "circuit:tg"

    def test_state_key_queue_exceeded_event(self):
        agg = EventAggregator(bus=EventBus())
        key = agg._state_key(QueueDepthExceeded(queue_name="staging"))
        assert key == "queue:staging"

    def test_state_key_queue_normal_event(self):
        agg = EventAggregator(bus=EventBus())
        assert agg._state_key(QueueDepthNormal(queue_name="staging")) == "queue:staging"


class TestEventAggregatorChanged:
    def test_first_call_returns_true(self):
        agg = EventAggregator(bus=EventBus())
        assert agg._changed("pool:x", "failed") is True

    def test_same_state_returns_false(self):
        agg = EventAggregator(bus=EventBus())
        agg._changed("pool:x", "failed")
        assert agg._changed("pool:x", "failed") is False

    def test_different_state_returns_true(self):
        agg = EventAggregator(bus=EventBus())
        agg._changed("pool:x", "failed")
        assert agg._changed("pool:x", "healthy") is True

    def test_updates_state_on_change(self):
        agg = EventAggregator(bus=EventBus())
        agg._changed("pool:x", "running")
        agg._changed("pool:x", "failed")
        assert agg._state["pool:x"] == "failed"


class TestEventAggregatorDedup:
    @pytest.mark.asyncio
    async def test_no_action_on_same_state_twice(self):
        """Emitting AgentFailed twice triggers monitoring action only once."""
        bus = EventBus()
        agg = EventAggregator(bus=bus)
        call_count = 0

        async def mock_action(event):
            nonlocal call_count
            call_count += 1

        agg._trigger_monitoring_action = mock_action
        await agg._handle(AgentFailed(agent_id="x", error="err"))
        await agg._handle(AgentFailed(agent_id="x", error="err"))
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_action_on_state_change(self):
        """Emitting AgentCompleted then AgentFailed triggers action twice."""
        bus = EventBus()
        agg = EventAggregator(bus=bus)
        call_count = 0

        async def mock_action(event):
            nonlocal call_count
            call_count += 1

        agg._trigger_monitoring_action = mock_action
        await agg._handle(AgentCompleted(agent_id="x", duration_ms=100))
        await agg._handle(AgentFailed(agent_id="x", error="err"))
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_circuit_state_change_triggers_action(self):
        bus = EventBus()
        agg = EventAggregator(bus=bus)
        actions = []

        async def mock_action(event):
            actions.append(event)

        agg._trigger_monitoring_action = mock_action
        event = CircuitStateChanged(
            platform="anthropic", old_state="closed", new_state="open"
        )
        await agg._handle(event)
        await agg._handle(event)  # duplicate
        assert len(actions) == 1
        assert actions[0].new_state == "open"


class TestEventAggregatorCrashRecovery:
    @pytest.mark.asyncio
    async def test_aggregator_continues_after_handler_exception(self, reset_bus):
        """EventAggregator survives an exception in _trigger_monitoring_action."""
        bus = EventBus()
        set_event_bus(bus)
        agg = EventAggregator(bus)

        async def always_raises(event):  # noqa: ARG001
            raise RuntimeError("monitoring action failed")

        agg._trigger_monitoring_action = always_raises

        task = asyncio.create_task(agg.run())
        try:
            # First event — causes exception in _trigger_monitoring_action
            bus.emit(AgentFailed(agent_id="test", error="boom"))
            await asyncio.sleep(0.05)

            # Aggregator must still be running after first exception
            assert not task.done(), (
                "Aggregator task should still be running after first exception"
            )

            # Second event with different key — aggregator must process it too
            second_processed = False

            async def track_second(event):  # noqa: ARG001
                nonlocal second_processed
                second_processed = True

            agg._trigger_monitoring_action = track_second
            bus.emit(AgentFailed(agent_id="other", error="boom2"))
            await asyncio.sleep(0.05)

            assert second_processed, (
                "Aggregator should have processed second event after first exception"
            )
            assert not task.done(), "Aggregator task should still be running"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
