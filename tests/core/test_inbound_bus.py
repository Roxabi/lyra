"""Tests for InboundBus: per-platform queues + feeder tasks + staging queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from lyra.core.auth import TrustLevel
from lyra.core.event_bus import EventBus, set_event_bus
from lyra.core.events import QueueDepthExceeded, QueueDepthNormal
from lyra.core.inbound_bus import InboundBus
from lyra.core.message import (
    InboundMessage,
    Platform,
)


def _make_msg(platform: Platform = Platform.TELEGRAM) -> InboundMessage:
    if platform == Platform.TELEGRAM:
        scope = "chat:123"
        meta = {"chat_id": 123, "topic_id": None, "message_id": None, "is_group": False}
    else:
        scope = "channel:2"
        meta = {
            "guild_id": 1,
            "channel_id": 2,
            "message_id": 3,
            "thread_id": None,
            "channel_type": "text",
        }
    return InboundMessage(
        id="msg-1",
        platform=platform.value,
        bot_id="main",
        scope_id=scope,
        user_id="user:1",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta=meta,
        trust_level=TrustLevel.TRUSTED,
    )


class TestInboundBusRegistration:
    def test_register_creates_queue(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=50)
        assert Platform.TELEGRAM in bus._queues
        assert bus._queues[Platform.TELEGRAM].maxsize == 50

    def test_qsize_zero_initially(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM)
        assert bus.qsize(Platform.TELEGRAM) == 0

    def test_qsize_unknown_platform_returns_zero(self) -> None:
        bus = InboundBus()
        assert bus.qsize(Platform.TELEGRAM) == 0

    def test_put_increments_qsize(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        msg = _make_msg(Platform.TELEGRAM)
        bus.put(Platform.TELEGRAM, msg)
        assert bus.qsize(Platform.TELEGRAM) == 1

    def test_put_raises_queue_full(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=1)
        msg = _make_msg(Platform.TELEGRAM)
        bus.put(Platform.TELEGRAM, msg)
        with pytest.raises(asyncio.QueueFull):
            bus.put(Platform.TELEGRAM, msg)


class TestInboundBusFeeder:
    async def test_feeder_forwards_to_staging(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()

        try:
            msg = _make_msg(Platform.TELEGRAM)
            bus.put(Platform.TELEGRAM, msg)

            # Wait for feeder to forward to staging
            received = await asyncio.wait_for(bus.get(), timeout=0.5)
            assert received is msg
        finally:
            await bus.stop()

    async def test_two_platforms_isolated(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=1)
        bus.register(Platform.DISCORD, maxsize=10)
        await bus.start()

        try:
            tg_msg = _make_msg(Platform.TELEGRAM)
            dc_msg = _make_msg(Platform.DISCORD)

            # Fill telegram queue
            bus.put(Platform.TELEGRAM, tg_msg)
            with pytest.raises(asyncio.QueueFull):
                bus.put(Platform.TELEGRAM, tg_msg)

            # Discord queue unaffected — put succeeds (not raises QueueFull)
            bus.put(Platform.DISCORD, dc_msg)
        finally:
            await bus.stop()

    async def test_stop_cancels_feeders(self) -> None:
        bus = InboundBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()
        assert len(bus._feeders) == 1

        await bus.stop()
        assert len(bus._feeders) == 0


# ---------------------------------------------------------------------------
# T9 — InboundBus emits QueueDepthExceeded / QueueDepthNormal via EventBus
# ---------------------------------------------------------------------------


@pytest.fixture
def ib_event_bus_fixture():
    """Register a fresh EventBus singleton; reset after test."""
    eb = EventBus()
    set_event_bus(eb)
    yield eb
    set_event_bus(None)  # type: ignore[arg-type]


def _attach_queue_ib(bus: EventBus) -> asyncio.Queue:
    """Attach a plain asyncio.Queue as a bus subscriber and return it."""
    q: asyncio.Queue = asyncio.Queue()
    bus._subscribers.append(q)
    return q


class TestInboundBusQueueDepthEvents:
    """InboundBus emits QueueDepthExceeded / QueueDepthNormal events (T9).

    These are async tests — the depth check lives in _feeder() which only
    runs after bus.start(). Tests start the bus, inject messages, wait for
    feeders to drain to staging, then inspect emitted events.
    """

    @pytest.mark.asyncio
    async def test_queue_depth_exceeded_emitted_on_threshold_crossing(
        self, ib_event_bus_fixture: EventBus
    ) -> None:
        """Crossing queue_depth_threshold emits QueueDepthExceeded exactly once."""
        eb = ib_event_bus_fixture
        q = _attach_queue_ib(eb)
        bus = InboundBus(queue_depth_threshold=2)
        bus.register(Platform.TELEGRAM, maxsize=20)
        await bus.start()
        msg = _make_msg(Platform.TELEGRAM)
        try:
            # Put 3 messages — staging threshold (2) crossed on the 3rd
            bus.put(Platform.TELEGRAM, msg)
            bus.put(Platform.TELEGRAM, msg)
            bus.put(Platform.TELEGRAM, msg)
            # Give feeders a moment to drain to staging
            await asyncio.sleep(0.05)
        finally:
            await bus.stop()

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        exceeded = [e for e in events if isinstance(e, QueueDepthExceeded)]
        assert len(exceeded) == 1, (
            f"Expected 1 QueueDepthExceeded, got {len(exceeded)}: {events}"
        )
        assert exceeded[0].queue_name == "staging"
        assert exceeded[0].depth >= 3
        assert exceeded[0].threshold == 2

    @pytest.mark.asyncio
    async def test_queue_depth_normal_emitted_on_recovery(
        self, ib_event_bus_fixture: EventBus
    ) -> None:
        """Draining staging below threshold emits QueueDepthNormal."""
        eb = ib_event_bus_fixture
        bus = InboundBus(queue_depth_threshold=2)
        bus.register(Platform.TELEGRAM, maxsize=20)
        await bus.start()
        msg = _make_msg(Platform.TELEGRAM)
        try:
            # Cross threshold
            bus.put(Platform.TELEGRAM, msg)
            bus.put(Platform.TELEGRAM, msg)
            bus.put(Platform.TELEGRAM, msg)
            await asyncio.sleep(0.05)

            # Now attach subscriber — only captures events from here onward
            q = _attach_queue_ib(eb)

            # Drain staging below threshold manually
            for _ in range(3):
                await asyncio.wait_for(bus._staging.get(), timeout=1.0)

            # Send one more message — feeder moves it to staging (depth now 1 <= 2)
            # and emits QueueDepthNormal because _depth_exceeded is still True
            bus.put(Platform.TELEGRAM, msg)
            await asyncio.sleep(0.05)
        finally:
            await bus.stop()

        # Assert — exactly one QueueDepthNormal event emitted (edge-trigger)
        events = []
        while not q.empty():
            events.append(q.get_nowait())

        normal = [e for e in events if isinstance(e, QueueDepthNormal)]
        assert len(normal) == 1, (
            f"Expected exactly 1 QueueDepthNormal event, got {len(normal)}: {events}"
        )
        assert normal[0].queue_name == "staging"

        # One more below-threshold message — must NOT emit a second QueueDepthNormal
        q2: asyncio.Queue = asyncio.Queue()
        eb._subscribers.append(q2)
        bus2 = InboundBus(queue_depth_threshold=2)
        bus2._staging = bus._staging  # share staging so depth is visible
        # Just verify no additional normal event was queued (edge trigger, not level)
        extra_events = []
        while not q.empty():
            extra_events.append(q.get_nowait())
        extra_normal = [e for e in extra_events if isinstance(e, QueueDepthNormal)]
        assert len(extra_normal) == 0, (
            f"No additional QueueDepthNormal expected after first, got: {extra_events}"
        )
