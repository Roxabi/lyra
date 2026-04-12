"""Tests for NatsLlmDriver.

Covers: complete(), stream(), is_alive(), heartbeat lifecycle.
All NATS interactions are mocked via AsyncMock.

AAA structure throughout:
  Arrange — set up mocks and driver state
  Act     — call the method under test
  Assert  — verify return value / side-effects

asyncio_mode = "auto" is configured project-wide in pyproject.toml.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent_config import ModelConfig
from lyra.llm.drivers.nats_driver import HB_TTL, NatsLlmDriver
from lyra.llm.events import ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_model_cfg() -> ModelConfig:
    return ModelConfig(backend="nats", model="qwen2.5-14b")


def make_driver(nc: AsyncMock | None = None, timeout: float = 5.0) -> NatsLlmDriver:
    if nc is None:
        nc = AsyncMock()
        nc.is_connected = True
        nc.new_inbox = MagicMock(return_value="_INBOX.test.abc123")
    return NatsLlmDriver(nc=nc, timeout=timeout)


def make_reply(data: dict) -> MagicMock:
    """Fake NATS reply message with .data as JSON bytes."""
    msg = MagicMock()
    msg.data = json.dumps(data).encode("utf-8")
    return msg


def make_chunk_msg(chunk: dict) -> MagicMock:
    """Fake NATS inbox message for stream chunks."""
    msg = MagicMock()
    msg.data = json.dumps(chunk).encode("utf-8")
    return msg


# ---------------------------------------------------------------------------
# 1. complete() happy path
# ---------------------------------------------------------------------------


class TestCompleteHappyPath:
    async def test_returns_llm_result_with_text(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.request = AsyncMock(
            return_value=make_reply(
                {
                    "request_id": "rid-1",
                    "result": "Hello, world!",
                    "session_id": "",
                    "error": "",
                    "retryable": True,
                }
            )
        )
        driver = make_driver(nc)
        model_cfg = make_model_cfg()

        # Act
        result = await driver.complete("pool:1", "hi", model_cfg, "sys")

        # Assert
        assert result.ok
        assert result.error == ""
        assert result.result == "Hello, world!"
        nc.request.assert_awaited_once()
        call_args = nc.request.call_args
        subject = call_args.args[0]
        payload = json.loads(call_args.args[1])
        assert subject == "lyra.llm.request"
        assert payload["stream"] is False
        assert payload["pool_id"] == "pool:1"
        assert payload["text"] == "hi"

    async def test_payload_includes_model_cfg_as_dict(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.request = AsyncMock(
            return_value=make_reply({"result": "ok", "error": "", "retryable": True})
        )
        driver = make_driver(nc)
        model_cfg = make_model_cfg()

        # Act
        await driver.complete("pool:1", "hi", model_cfg, "system")

        # Assert — model_cfg serialised as dict in payload
        payload = json.loads(nc.request.call_args.args[1])
        assert isinstance(payload["model_cfg"], dict)
        assert payload["model_cfg"]["model"] == "qwen2.5-14b"

    async def test_passes_messages_list(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.request = AsyncMock(
            return_value=make_reply({"result": "ok", "error": "", "retryable": True})
        )
        driver = make_driver(nc)
        msgs = [{"role": "user", "content": "hello"}]

        # Act
        await driver.complete("p", "hi", make_model_cfg(), "sys", messages=msgs)

        # Assert
        payload = json.loads(nc.request.call_args.args[1])
        assert payload["messages"] == msgs


# ---------------------------------------------------------------------------
# 2. complete() timeout
# ---------------------------------------------------------------------------


class TestCompleteTimeout:
    async def test_timeout_returns_error_result_retryable(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.request = AsyncMock(side_effect=TimeoutError("timed out"))
        driver = make_driver(nc, timeout=1.0)

        # Act
        result = await driver.complete("pool:1", "hi", make_model_cfg(), "sys")

        # Assert
        assert not result.ok
        assert result.retryable is True
        assert "timeout" in result.error.lower()

    async def test_transport_error_returns_retryable_error(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.request = AsyncMock(side_effect=RuntimeError("connection refused"))
        driver = make_driver(nc)

        # Act
        result = await driver.complete("pool:1", "hi", make_model_cfg(), "sys")

        # Assert
        assert not result.ok
        assert result.retryable is True
        assert "NATS" in result.error or "connection" in result.error.lower()


# ---------------------------------------------------------------------------
# 3. complete() worker error with retryable=False
# ---------------------------------------------------------------------------


class TestCompleteWorkerError:
    async def test_worker_error_non_retryable_preserved(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.request = AsyncMock(
            return_value=make_reply(
                {
                    "error": "quota exhausted",
                    "retryable": False,
                }
            )
        )
        driver = make_driver(nc)

        # Act
        result = await driver.complete("pool:1", "hi", make_model_cfg(), "sys")

        # Assert
        assert not result.ok
        assert result.retryable is False
        assert "quota" in result.error

    async def test_worker_error_retryable_true_preserved(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.request = AsyncMock(
            return_value=make_reply({"error": "model overloaded", "retryable": True})
        )
        driver = make_driver(nc)

        # Act
        result = await driver.complete("pool:1", "hi", make_model_cfg(), "sys")

        # Assert
        assert not result.ok
        assert result.retryable is True


# ---------------------------------------------------------------------------
# 4. stream() yields events in order, terminates on done=True
# ---------------------------------------------------------------------------


class TestStreamHappyPath:
    async def test_yields_text_tool_result_events_in_order(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.new_inbox = MagicMock(return_value="_INBOX.test.x")

        chunks = [
            {"event_type": "text", "text": "Hello", "done": False},
            {
                "event_type": "tool_use",
                "tool_name": "get_time",
                "tool_id": "t1",
                "input": {},
                "done": False,
            },
            {
                "event_type": "result",
                "is_error": False,
                "duration_ms": 500,
                "done": True,
            },
        ]

        # Subscription callback captured so we can feed chunks manually.
        captured_cb: Any = None

        async def fake_subscribe(subject, cb=None):
            nonlocal captured_cb
            captured_cb = cb
            sub = AsyncMock()
            return sub

        nc.subscribe = AsyncMock(side_effect=fake_subscribe)
        nc.publish = AsyncMock()

        driver = make_driver(nc)

        async def collect() -> list:
            events = []
            gen = await driver.stream("p", "hi", make_model_cfg(), "sys")
            # Feed chunks into the subscription after publishing
            task = asyncio.create_task(_drain(gen, events))
            await asyncio.sleep(0)  # yield so task starts
            for chunk in chunks:
                await captured_cb(make_chunk_msg(chunk))
            await task
            return events

        async def _drain(gen, events):
            async for ev in gen:
                events.append(ev)

        # Act
        events = await collect()

        # Assert
        assert len(events) == 3
        assert isinstance(events[0], TextLlmEvent)
        assert events[0].text == "Hello"
        assert isinstance(events[1], ToolUseLlmEvent)
        assert events[1].tool_name == "get_time"
        assert isinstance(events[2], ResultLlmEvent)
        assert events[2].is_error is False
        assert events[2].duration_ms == 500

    async def test_stream_request_has_stream_true(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.new_inbox = MagicMock(return_value="_INBOX.test.y")

        captured_cb: Any = None

        async def fake_subscribe(subject, cb=None):
            nonlocal captured_cb
            captured_cb = cb
            return AsyncMock()

        nc.subscribe = AsyncMock(side_effect=fake_subscribe)
        nc.publish = AsyncMock()

        driver = make_driver(nc)

        async def collect():
            events = []
            gen = await driver.stream("p", "hi", make_model_cfg(), "sys")
            task = asyncio.create_task(_drain(gen, events))
            await asyncio.sleep(0)
            # Send a done result chunk
            await captured_cb(
                make_chunk_msg(
                    {
                        "event_type": "result",
                        "is_error": False,
                        "duration_ms": 0,
                        "done": True,
                    }
                )
            )
            await task
            return events

        async def _drain(gen, events):
            async for ev in gen:
                events.append(ev)

        await collect()

        # Assert publish was called with stream=true in payload
        assert nc.publish.await_count == 1
        payload = json.loads(nc.publish.call_args.args[1])
        assert payload["stream"] is True


# ---------------------------------------------------------------------------
# 5. stream() cancellation cleans up inbox subscription
# ---------------------------------------------------------------------------


class TestStreamCancellation:
    async def test_unsubscribe_called_on_cancellation(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        nc.new_inbox = MagicMock(return_value="_INBOX.test.cancel")

        sub_mock = AsyncMock()

        async def fake_subscribe(subject, cb=None):
            return sub_mock

        nc.subscribe = AsyncMock(side_effect=fake_subscribe)
        nc.publish = AsyncMock()

        driver = make_driver(nc)

        async def run_and_cancel():
            gen = await driver.stream("p", "hi", make_model_cfg(), "sys")
            task = asyncio.create_task(_consume(gen))
            await asyncio.sleep(0)  # let task start waiting on queue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        async def _consume(gen):
            async for _ in gen:
                pass

        # Act
        await run_and_cancel()

        # Assert — unsubscribe was awaited (cleanup in finally block)
        sub_mock.unsubscribe.assert_awaited_once()


# ---------------------------------------------------------------------------
# 6. is_alive() — no heartbeat received
# ---------------------------------------------------------------------------


class TestIsAliveNoHeartbeat:
    def test_is_alive_false_when_no_heartbeat(self) -> None:
        # Arrange
        nc = MagicMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)
        # _worker_freshness is empty — no heartbeat ever received

        # Act / Assert
        assert driver.is_alive("pool:1") is False

    def test_is_alive_false_when_nc_not_connected(self) -> None:
        # Arrange
        nc = MagicMock()
        nc.is_connected = False
        driver = NatsLlmDriver(nc=nc)
        driver._worker_freshness["worker-1"] = time.monotonic()  # fresh HB

        # Act / Assert
        assert driver.is_alive("pool:1") is False


# ---------------------------------------------------------------------------
# 7. is_alive() — True after fresh heartbeat
# ---------------------------------------------------------------------------


class TestIsAliveAfterHeartbeat:
    def test_is_alive_true_after_fresh_heartbeat(self) -> None:
        # Arrange
        nc = MagicMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)
        driver._worker_freshness["worker-1"] = time.monotonic()  # just now

        # Act / Assert
        assert driver.is_alive("pool:1") is True

    async def test_is_alive_true_after_processing_heartbeat_msg(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)
        hb_msg = make_chunk_msg({"worker_id": "machine2-gpu", "gpu": "RTX 5070Ti"})

        # Act — simulate receiving a heartbeat
        await driver._on_heartbeat(hb_msg)

        # Assert
        assert driver.is_alive("pool:1") is True

    async def test_heartbeat_missing_worker_id_ignored(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)
        bad_msg = make_chunk_msg({"gpu": "RTX 5070Ti"})  # no worker_id

        # Act
        await driver._on_heartbeat(bad_msg)

        # Assert — nothing was added
        assert driver._worker_freshness == {}
        assert driver.is_alive("pool:1") is False


# ---------------------------------------------------------------------------
# 8. Stale heartbeat → is_alive() False
# ---------------------------------------------------------------------------


class TestIsAliveStaleHeartbeat:
    def test_stale_heartbeat_beyond_ttl_returns_false(self) -> None:
        # Arrange
        nc = MagicMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)
        # Heartbeat 35 seconds ago — beyond HB_TTL (30s)
        driver._worker_freshness["worker-1"] = time.monotonic() - (HB_TTL + 5.0)

        # Act / Assert
        assert driver.is_alive("pool:1") is False

    def test_stale_entries_pruned_beyond_ttl_times_two(self) -> None:
        # Arrange — entry older than TTL*2 should be pruned
        nc = MagicMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)
        ancient = time.monotonic() - (HB_TTL * 2 + 10.0)
        fresh = time.monotonic() - 5.0
        driver._worker_freshness["ancient"] = ancient
        driver._worker_freshness["fresh"] = fresh

        # Act
        result = driver._any_worker_alive()

        # Assert — "ancient" pruned, "fresh" retained
        assert result is True
        assert "ancient" not in driver._worker_freshness
        assert "fresh" in driver._worker_freshness

    def test_stale_only_worker_pruned_returns_false(self) -> None:
        # Arrange
        nc = MagicMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)
        driver._worker_freshness["worker-1"] = time.monotonic() - (HB_TTL * 2 + 10.0)

        # Act
        result = driver._any_worker_alive()

        # Assert — pruned and false
        assert result is False
        assert driver._worker_freshness == {}


# ---------------------------------------------------------------------------
# Lifecycle: start / stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_start_subscribes_to_heartbeat_pattern(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)

        # Act
        await driver.start()

        # Assert
        nc.subscribe.assert_awaited_once()
        subject = nc.subscribe.call_args.args[0]
        assert subject == "lyra.llm.health.*"

    async def test_start_is_idempotent(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)

        # Act — call twice
        await driver.start()
        await driver.start()

        # Assert — subscribed only once
        assert nc.subscribe.await_count == 1

    async def test_stop_unsubscribes(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        sub_mock = AsyncMock()
        nc.subscribe = AsyncMock(return_value=sub_mock)
        driver = NatsLlmDriver(nc=nc)
        await driver.start()

        # Act
        await driver.stop()

        # Assert
        sub_mock.unsubscribe.assert_awaited_once()
        assert driver._hb_sub is None

    async def test_stop_without_start_is_noop(self) -> None:
        # Arrange
        nc = AsyncMock()
        nc.is_connected = True
        driver = NatsLlmDriver(nc=nc)

        # Act / Assert — must not raise
        await driver.stop()


# ---------------------------------------------------------------------------
# 9. stream() inbox timeout yields error ResultLlmEvent
# ---------------------------------------------------------------------------


class TestStreamInboxTimeout:
    async def test_stream_inbox_timeout_yields_error_result(self) -> None:
        # Arrange — very short timeout so wait_for expires immediately
        nc = AsyncMock()
        nc.is_connected = True
        nc.new_inbox = MagicMock(return_value="_INBOX.test.timeout")

        sub_mock = AsyncMock()

        async def fake_subscribe(subject, cb=None):
            return sub_mock

        nc.subscribe = AsyncMock(side_effect=fake_subscribe)
        nc.publish = AsyncMock()

        driver = make_driver(nc, timeout=0.01)

        # Act — nothing published into the queue, so wait_for raises TimeoutError
        events = []
        gen = await driver.stream("p", "hi", make_model_cfg(), "sys")
        async for ev in gen:
            events.append(ev)

        # Assert — exactly one error ResultLlmEvent
        assert len(events) == 1
        assert isinstance(events[0], ResultLlmEvent)
        assert events[0].is_error is True
        assert events[0].duration_ms == 0

        # Assert inbox subscription was cleaned up
        sub_mock.unsubscribe.assert_awaited_once()


# ---------------------------------------------------------------------------
# 10. Defensive streaming branches
# ---------------------------------------------------------------------------


class TestStreamDefensiveBranches:
    async def test_stream_unknown_event_type_silently_skipped(self) -> None:
        # Arrange — "ping" chunk followed by result chunk
        nc = AsyncMock()
        nc.is_connected = True
        nc.new_inbox = MagicMock(return_value="_INBOX.test.unknown")

        chunks = [
            {"event_type": "ping", "done": False},
            {"event_type": "result", "is_error": False, "duration_ms": 0, "done": True},
        ]

        captured_cb: Any = None

        async def fake_subscribe(subject, cb=None):
            nonlocal captured_cb
            captured_cb = cb
            return AsyncMock()

        nc.subscribe = AsyncMock(side_effect=fake_subscribe)
        nc.publish = AsyncMock()

        driver = make_driver(nc)

        async def collect() -> list:
            events: list = []
            gen = await driver.stream("p", "hi", make_model_cfg(), "sys")
            task = asyncio.create_task(_drain(gen, events))
            await asyncio.sleep(0)
            for chunk in chunks:
                await captured_cb(make_chunk_msg(chunk))
            await task
            return events

        async def _drain(gen, events):
            async for ev in gen:
                events.append(ev)

        events = await collect()

        # Assert — only the ResultLlmEvent (ping silently skipped)
        assert events == [ResultLlmEvent(is_error=False, duration_ms=0)]

    async def test_stream_done_true_on_non_result_emits_synthetic_result(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — text chunk with done=True (no result chunk)
        nc = AsyncMock()
        nc.is_connected = True
        nc.new_inbox = MagicMock(return_value="_INBOX.test.synth")

        chunks = [
            {"event_type": "text", "text": "hi", "done": True},
        ]

        captured_cb: Any = None

        async def fake_subscribe(subject, cb=None):
            nonlocal captured_cb
            captured_cb = cb
            return AsyncMock()

        nc.subscribe = AsyncMock(side_effect=fake_subscribe)
        nc.publish = AsyncMock()

        driver = make_driver(nc)

        async def collect() -> list:
            events: list = []
            gen = await driver.stream("p", "hi", make_model_cfg(), "sys")
            task = asyncio.create_task(_drain(gen, events))
            await asyncio.sleep(0)
            for chunk in chunks:
                await captured_cb(make_chunk_msg(chunk))
            await task
            return events

        async def _drain(gen, events):
            async for ev in gen:
                events.append(ev)

        with caplog.at_level("WARNING", logger="lyra.llm.drivers.nats_driver"):
            events = await collect()

        # Assert events: TextLlmEvent then synthetic ResultLlmEvent
        assert events == [
            TextLlmEvent(text="hi"),
            ResultLlmEvent(is_error=False, duration_ms=0),
        ]

        # Assert warning was emitted for the done=True on non-result chunk
        assert any(
            "done=True" in record.message and "event_type" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )
