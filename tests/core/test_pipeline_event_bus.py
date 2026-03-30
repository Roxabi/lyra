"""Unit tests for PipelineEventBus + pipeline events (#432)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging

import pytest

from lyra.core.hub.audit_consumer import AuditConsumer
from lyra.core.hub.event_bus import PipelineEventBus
from lyra.core.hub.pipeline_events import (
    CommandDispatched,
    MessageDropped,
    MessageReceived,
    PipelineEvent,
    PoolSubmitted,
    StageCompleted,
)


def _make_event(**overrides: object) -> MessageReceived:
    defaults = {
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
        assert any(
            "queue full" in r.message for r in caplog.records
        ), f"Expected queue full warning, got: {[r.message for r in caplog.records]}"

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
        warn_count = sum(
            1 for r in caplog.records if "queue full" in r.message
        )
        assert warn_count == 1

    def test_external_subscriber(self) -> None:
        """Plugin-like external code can subscribe and receive events."""
        bus = PipelineEventBus()
        q = bus.subscribe()
        bus.emit(_make_event())
        event = q.get_nowait()
        assert isinstance(event, PipelineEvent)
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
    async def test_logs_structured_json(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
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
