"""Unit tests for PipelineEventBus + pipeline events (#432)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging

import pytest

from lyra.core.hub.event_bus import PipelineEventBus
from lyra.core.hub.middleware import (
    MiddlewarePipeline,
    PipelineContext,
)
from lyra.core.hub.pipeline.audit_consumer import AuditConsumer
from lyra.core.hub.pipeline.message_pipeline import Action, PipelineResult
from lyra.core.hub.pipeline.pipeline_events import (
    CommandDispatched,
    MessageDropped,
    MessageReceived,
    PipelineEvent,
    PoolSubmitted,
    StageCompleted,
)
from tests.conftest import yield_once
from tests.core.conftest import _make_hub, make_inbound_message


def _make_event(**overrides: str) -> MessageReceived:
    defaults: dict[str, str] = {
        "msg_id": "test-1",
        "stage": "inbound",
        "platform": "telegram",
        "user_id": "u1",
        "scope_id": "s1",
    }
    defaults.update(overrides)
    return MessageReceived(**defaults)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────
# Pipeline events
# ──────────────────────────────────────────────────────────────────────


class TestPipelineEvents:
    def test_events_are_frozen(self) -> None:
        event = _make_event()
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.msg_id = "changed"  # type: ignore[misc]

    def test_all_subtypes_inherit_base(self) -> None:
        for cls in (
            MessageReceived,
            StageCompleted,
            MessageDropped,
            CommandDispatched,
            PoolSubmitted,
        ):
            assert issubclass(cls, PipelineEvent)

    def test_asdict_produces_dict(self) -> None:
        event = _make_event()
        d = dataclasses.asdict(event)
        assert d["msg_id"] == "test-1"
        assert d["platform"] == "telegram"
        assert "timestamp" in d


# ──────────────────────────────────────────────────────────────────────
# PipelineEventBus
# ──────────────────────────────────────────────────────────────────────


class TestPipelineEventBus:
    def test_emit_no_subscribers(self) -> None:
        bus = PipelineEventBus()
        bus.emit(_make_event())  # no error

    def test_emit_single_subscriber(self) -> None:
        bus = PipelineEventBus()
        q = bus.subscribe()
        bus.emit(_make_event())
        assert q.qsize() == 1
        event = q.get_nowait()
        assert isinstance(event, MessageReceived)
        assert event.msg_id == "test-1"

    def test_emit_fan_out_multiple_subscribers(self) -> None:
        bus = PipelineEventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        q3 = bus.subscribe()
        bus.emit(_make_event())
        assert q1.qsize() == 1
        assert q2.qsize() == 1
        assert q3.qsize() == 1

    def test_queue_full_drops_event(self) -> None:
        bus = PipelineEventBus(maxsize=1)
        q = bus.subscribe()
        bus.emit(_make_event(msg_id="first"))
        bus.emit(_make_event(msg_id="second"))  # dropped
        assert q.qsize() == 1
        event = q.get_nowait()
        assert event.msg_id == "first"

    def test_queue_full_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        bus = PipelineEventBus(maxsize=1)
        bus.subscribe()  # subscriber needed to trigger QueueFull
        # Reset last warn time to ensure warning fires
        bus._last_warn.clear()
        with caplog.at_level(logging.WARNING):
            bus.emit(_make_event())
            bus.emit(_make_event())  # triggers warning
        assert any("queue full" in r.message for r in caplog.records), (
            f"Expected queue full warning, got: {[r.message for r in caplog.records]}"
        )

    def test_queue_full_warning_rate_limited(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        bus = PipelineEventBus(maxsize=1)
        bus.subscribe()  # subscriber needed to trigger QueueFull
        bus._last_warn.clear()
        with caplog.at_level(logging.WARNING):
            bus.emit(_make_event())
            # Emit 5 more — all dropped, but only 1 warning
            for _ in range(5):
                bus.emit(_make_event())
        warn_count = sum(1 for r in caplog.records if "queue full" in r.message)
        assert warn_count == 1

    def test_external_subscriber(self) -> None:
        """Plugin-like external code can subscribe and receive events."""
        bus = PipelineEventBus()
        q = bus.subscribe()
        bus.emit(_make_event())
        event = q.get_nowait()
        assert isinstance(event, MessageReceived)
        assert event.platform == "telegram"

    def test_queue_full_does_not_affect_other_subscribers(self) -> None:
        bus = PipelineEventBus(maxsize=1)
        q_slow = bus.subscribe()
        q_fast = bus.subscribe()
        bus.emit(_make_event(msg_id="first"))
        # q_slow is now full
        _ = q_fast.get_nowait()  # drain fast subscriber
        bus.emit(_make_event(msg_id="second"))
        # q_slow dropped second, q_fast got it
        assert q_fast.qsize() == 1
        assert q_fast.get_nowait().msg_id == "second"
        assert q_slow.qsize() == 1
        assert q_slow.get_nowait().msg_id == "first"

    def test_subscribe_returns_queue(self) -> None:
        bus = PipelineEventBus()
        q = bus.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_configurable_maxsize(self) -> None:
        bus = PipelineEventBus(maxsize=5)
        q = bus.subscribe()
        assert q.maxsize == 5


# ──────────────────────────────────────────────────────────────────────
# AuditConsumer
# ──────────────────────────────────────────────────────────────────────


class TestAuditConsumer:
    async def test_logs_structured_json(self, caplog: pytest.LogCaptureFixture) -> None:
        bus = PipelineEventBus()
        q = bus.subscribe()
        consumer = AuditConsumer(q)

        bus.emit(_make_event(msg_id="audit-1", stage="inbound"))

        with caplog.at_level(logging.INFO):
            task = asyncio.create_task(consumer.run())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        log_records = [
            r for r in caplog.records if r.getMessage().startswith("pipeline.")
        ]
        assert len(log_records) >= 1
        record = log_records[0]
        assert "pipeline.inbound" in record.getMessage()
        extra_event = record.__dict__.get("event", {})
        assert extra_event["msg_id"] == "audit-1"
        assert extra_event["platform"] == "telegram"

    async def test_drains_multiple_events(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        bus = PipelineEventBus()
        q = bus.subscribe()
        consumer = AuditConsumer(q)

        bus.emit(_make_event(msg_id="e1"))
        bus.emit(_make_event(msg_id="e2"))
        bus.emit(_make_event(msg_id="e3"))

        with caplog.at_level(logging.INFO):
            task = asyncio.create_task(consumer.run())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        log_records = [
            r for r in caplog.records if r.getMessage().startswith("pipeline.")
        ]
        assert len(log_records) == 3

    async def test_shutdown_is_best_effort(self) -> None:
        """On cancellation, consumer stops — no active drain of remaining items."""
        bus = PipelineEventBus()
        q = bus.subscribe()
        consumer = AuditConsumer(q)

        # Start consumer, let it block on empty queue, then cancel
        task = asyncio.create_task(consumer.run())
        await yield_once()  # consumer is now awaiting queue.get()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # After cancel, items added to the queue are not processed
        bus.emit(_make_event(msg_id="post-cancel"))
        assert q.qsize() == 1  # event sits unprocessed — no drain


# ──────────────────────────────────────────────────────────────────────
# PipelineContext.emit() guard
# ──────────────────────────────────────────────────────────────────────


class TestPipelineContextEmit:
    def test_emit_noop_when_bus_is_none(self) -> None:
        """ctx.emit() is a no-op when event_bus=None — no exception."""
        hub = _make_hub()
        ctx = PipelineContext(hub=hub, event_bus=None)
        ctx.emit(_make_event())  # must not raise

    def test_emit_forwards_to_bus(self) -> None:
        """ctx.emit() forwards event to bus when configured."""
        hub = _make_hub()
        bus = PipelineEventBus()
        q = bus.subscribe()
        ctx = PipelineContext(hub=hub, event_bus=bus)
        ctx.emit(_make_event(msg_id="ctx-test"))
        assert q.qsize() == 1
        assert q.get_nowait().msg_id == "ctx-test"


# ──────────────────────────────────────────────────────────────────────
# MiddlewarePipeline emission integration
# ──────────────────────────────────────────────────────────────────────


class _PassthroughMiddleware:
    """Stub middleware that always calls next()."""

    async def __call__(self, msg, ctx, next):
        return await next(msg, ctx)


class _DropMiddleware:
    """Stub middleware that always drops."""

    async def __call__(self, msg, ctx, next):
        ctx.emit(
            MessageDropped(
                msg_id=msg.id,
                stage=type(self).__name__,
                reason="test_drop",
            )
        )
        return PipelineResult(action=Action.DROP)


class TestMiddlewarePipelineEmission:
    async def test_emits_message_received_and_stage_completed(self) -> None:
        """Pipeline emits MessageReceived + StageCompleted per stage."""
        bus = PipelineEventBus()
        q = bus.subscribe()
        hub = _make_hub()

        pipeline = MiddlewarePipeline(
            [_PassthroughMiddleware(), _PassthroughMiddleware()],
            hub,
            event_bus=bus,
        )
        msg = make_inbound_message(platform="telegram")
        await pipeline.process(msg)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        # MessageReceived first, then StageCompleted per middleware
        assert isinstance(events[0], MessageReceived)
        assert events[0].stage == "inbound"
        assert events[0].platform == "telegram"

        stage_completed = [e for e in events if isinstance(e, StageCompleted)]
        assert len(stage_completed) == 2
        assert stage_completed[0].stage == "_PassthroughMiddleware"
        assert stage_completed[1].stage == "_PassthroughMiddleware"
        assert all(e.duration_ms >= 0 for e in stage_completed)

    async def test_stage_name_uses_class_name(self) -> None:
        """StageCompleted.stage uses type(mw).__name__."""
        bus = PipelineEventBus()
        q = bus.subscribe()
        hub = _make_hub()

        pipeline = MiddlewarePipeline([_DropMiddleware()], hub, event_bus=bus)
        msg = make_inbound_message(platform="telegram")
        await pipeline.process(msg)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        stage_completed = [e for e in events if isinstance(e, StageCompleted)]
        assert len(stage_completed) == 1
        assert stage_completed[0].stage == "_DropMiddleware"

    async def test_drop_emits_message_dropped(self) -> None:
        """Dropping middleware emits MessageDropped with reason."""
        bus = PipelineEventBus()
        q = bus.subscribe()
        hub = _make_hub()

        pipeline = MiddlewarePipeline([_DropMiddleware()], hub, event_bus=bus)
        msg = make_inbound_message(platform="telegram")
        await pipeline.process(msg)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        dropped = [e for e in events if isinstance(e, MessageDropped)]
        assert len(dropped) == 1
        assert dropped[0].reason == "test_drop"
        assert dropped[0].stage == "_DropMiddleware"

    async def test_no_events_when_bus_is_none(self) -> None:
        """Pipeline with event_bus=None emits nothing — no errors."""
        hub = _make_hub()
        pipeline = MiddlewarePipeline([_PassthroughMiddleware()], hub, event_bus=None)
        msg = make_inbound_message(platform="telegram")
        result = await pipeline.process(msg)
        assert result.action == Action.DROP  # end of chain

    async def test_real_drop_stage_emits_events(self) -> None:
        """ValidatePlatformMiddleware emits MessageDropped for unknown platform."""
        from lyra.core.hub.middleware.middleware_stages import (
            ValidatePlatformMiddleware,
        )

        bus = PipelineEventBus()
        q = bus.subscribe()
        hub = _make_hub()

        pipeline = MiddlewarePipeline(
            [ValidatePlatformMiddleware()], hub, event_bus=bus
        )
        msg = make_inbound_message(platform="unknown_platform")
        await pipeline.process(msg)

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        dropped = [e for e in events if isinstance(e, MessageDropped)]
        assert len(dropped) == 1
        assert dropped[0].reason == "unknown_platform"
        assert dropped[0].stage == "ValidatePlatformMiddleware"


# ──────────────────────────────────────────────────────────────────────
# EventBusConfig
# ──────────────────────────────────────────────────────────────────────


class TestEventBusConfig:
    def test_default_queue_maxsize(self) -> None:
        from lyra.bootstrap.factory.config import EventBusConfig

        cfg = EventBusConfig()
        assert cfg.queue_maxsize == 1000

    def test_load_with_explicit_value(self) -> None:
        from lyra.bootstrap.factory.config import _load_event_bus_config

        cfg = _load_event_bus_config({"event_bus": {"queue_maxsize": 42}})
        assert cfg.queue_maxsize == 42

    def test_load_with_empty_config(self) -> None:
        from lyra.bootstrap.factory.config import _load_event_bus_config

        cfg = _load_event_bus_config({})
        assert cfg.queue_maxsize == 1000
