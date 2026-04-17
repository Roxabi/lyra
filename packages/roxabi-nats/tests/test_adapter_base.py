"""RED-phase tests for NatsAdapterBase (issue #582).

NatsAdapterBase does not exist yet — all tests are expected to fail with
ImportError until the implementation is in place.

Covers:
- T2: Construction validation (valid args, bad subject, bad queue_group, field storage)
- T3: _validate_envelope (v1 accepted, version mismatch dropped, missing field,
      counter keyed by envelope_name)
- T4: _shutdown (drain before close, no unsubscribe call)
- T5: health() return shape, connected flag, uptime_s before/after _started_at
- T6: run() signal handler wiring and _wait_ready invocation
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from roxabi_nats.adapter_base import NatsAdapterBase  # ImportError expected (RED)

# ---------------------------------------------------------------------------
# Concrete subclass used across all test classes
# ---------------------------------------------------------------------------


class _ConcreteAdapter(NatsAdapterBase):
    async def handle(self, msg: object, payload: dict) -> None:  # noqa: D102
        pass


# ---------------------------------------------------------------------------
# T2 — Construction
# ---------------------------------------------------------------------------


class TestNatsAdapterBaseConstruction:
    """T2 — __init__ validates tokens and stores all fields."""

    def test_valid_args_no_error(self) -> None:
        """Constructing with valid tokens raises no exception."""
        # Arrange / Act / Assert — no exception raised
        adapter = _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )
        assert adapter is not None

    def test_bad_subject_raises_value_error(self) -> None:
        """Subject containing a space triggers ValueError from validate_nats_token."""
        # Arrange
        bad_subject = "bad subject"

        # Act / Assert
        with pytest.raises(ValueError, match="subject"):
            _ConcreteAdapter(
                subject=bad_subject,
                queue_group="telegram_workers",
                envelope_name="InboundMessage",
                schema_version=1,
            )

    def test_bad_queue_group_raises_value_error(self) -> None:
        """Queue group with a space triggers ValueError from validate_nats_token."""
        # Arrange
        bad_group = "bad group"

        # Act / Assert
        with pytest.raises(ValueError, match="queue_group"):
            _ConcreteAdapter(
                subject="lyra.inbound.telegram.main",
                queue_group=bad_group,
                envelope_name="InboundMessage",
                schema_version=1,
            )

    def test_all_fields_stored_correctly(self) -> None:
        """All constructor arguments are stored as instance attributes."""
        # Arrange
        subject = "lyra.inbound.telegram.main"
        queue_group = "telegram_workers"
        envelope_name = "InboundMessage"
        schema_version = 2
        timeout = 15.0
        drain_timeout = 10.0

        # Act
        adapter = _ConcreteAdapter(
            subject=subject,
            queue_group=queue_group,
            envelope_name=envelope_name,
            schema_version=schema_version,
            timeout=timeout,
            drain_timeout=drain_timeout,
        )

        # Assert
        assert adapter.subject == subject
        assert adapter.queue_group == queue_group
        assert adapter.envelope_name == envelope_name
        assert adapter.schema_version == schema_version
        assert adapter.timeout == timeout
        assert adapter.drain_timeout == drain_timeout
        assert adapter._nc is None
        assert adapter._drop_count == {}
        assert adapter._started_at is None

    def test_default_timeout_values(self) -> None:
        """Default timeout and drain_timeout are 30.0 when not supplied."""
        # Arrange / Act
        adapter = _ConcreteAdapter(
            subject="lyra.inbound.discord.main",
            queue_group="discord_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )

        # Assert
        assert adapter.timeout == 30.0
        assert adapter.drain_timeout == 30.0


# ---------------------------------------------------------------------------
# T3 — _validate_envelope
# ---------------------------------------------------------------------------


class TestValidateEnvelope:
    """T3 — _validate_envelope delegates to check_schema_version correctly."""

    def _make_adapter(self, *, schema_version: int = 1) -> _ConcreteAdapter:
        return _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=schema_version,
        )

    def test_v1_payload_accepted(self) -> None:
        """Payload with schema_version == expected is accepted (returns True)."""
        # Arrange
        adapter = self._make_adapter(schema_version=1)
        payload = {"schema_version": 1, "data": "hello"}

        # Act
        result = adapter._validate_envelope(payload)

        # Assert
        assert result is True
        assert adapter._drop_count == {}

    def test_higher_version_payload_dropped(self) -> None:
        """Payload with schema_version > expected is dropped (returns False)."""
        # Arrange
        adapter = self._make_adapter(schema_version=1)
        payload = {"schema_version": 2, "data": "hello"}

        # Act
        result = adapter._validate_envelope(payload)

        # Assert
        assert result is False

    def test_drop_increments_drop_count(self) -> None:
        """A dropped envelope increments _drop_count keyed by (envelope, kind)."""
        # Arrange
        adapter = self._make_adapter(schema_version=1)
        payload = {"schema_version": 2, "data": "hello"}

        # Act
        adapter._validate_envelope(payload)

        # Assert — counter key carries the kind suffix so schema and contract
        # telemetry stay separable
        assert adapter._drop_count.get("InboundMessage:schema", 0) == 1

    def test_multiple_drops_accumulate_count(self) -> None:
        """Repeated drops accumulate the counter for the same (envelope, kind)."""
        # Arrange
        adapter = self._make_adapter(schema_version=1)
        payload = {"schema_version": 2}

        # Act
        adapter._validate_envelope(payload)
        adapter._validate_envelope(payload)
        adapter._validate_envelope(payload)

        # Assert
        assert adapter._drop_count["InboundMessage:schema"] == 3

    def test_missing_schema_version_treated_as_v1(self) -> None:
        """Payload without schema_version key is accepted as legacy v1."""
        # Arrange
        adapter = self._make_adapter(schema_version=1)
        payload = {"data": "legacy message"}

        # Act
        result = adapter._validate_envelope(payload)

        # Assert — treated as v1, no drop
        assert result is True
        assert adapter._drop_count == {}

    def test_envelope_name_used_as_counter_key(self) -> None:
        """_drop_count is keyed by envelope_name, not by subject."""
        # Arrange
        adapter = _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="CustomEnvelope",
            schema_version=1,
        )
        payload = {"schema_version": 3}

        # Act
        adapter._validate_envelope(payload)

        # Assert — key is "CustomEnvelope:schema", not the subject
        assert "CustomEnvelope:schema" in adapter._drop_count
        assert adapter._drop_count["CustomEnvelope:schema"] == 1

    def test_higher_contract_version_dropped(self) -> None:
        """Payload with contract_version > hub's CONTRACT_VERSION is dropped (#707)."""
        # Arrange — schema is fine, contract is from the future
        adapter = self._make_adapter(schema_version=1)
        payload = {"schema_version": 1, "contract_version": "2", "data": "hello"}

        # Act
        result = adapter._validate_envelope(payload)

        # Assert — dropped on the contract check, counter incremented under
        # the contract key (not the schema key)
        assert result is False
        assert adapter._drop_count.get("InboundMessage:contract", 0) == 1
        assert "InboundMessage:schema" not in adapter._drop_count

    def test_invalid_schema_version_short_circuits_contract_check(self) -> None:
        """Schema failure must short-circuit — contract check never runs."""
        # Arrange — schema is too new; contract is valid but shouldn't matter
        adapter = self._make_adapter(schema_version=1)
        payload = {"schema_version": 2, "contract_version": "1", "data": "hello"}

        # Act
        result = adapter._validate_envelope(payload)

        # Assert — exactly one drop, under the schema key; the contract check
        # never incremented its own counter because the guard short-circuited
        assert result is False
        assert adapter._drop_count == {"InboundMessage:schema": 1}

    def test_equal_contract_version_accepted(self) -> None:
        """Payload with contract_version == hub's CONTRACT_VERSION is accepted."""
        # Arrange
        adapter = self._make_adapter(schema_version=1)
        payload = {"schema_version": 1, "contract_version": "1", "data": "hello"}

        # Act
        result = adapter._validate_envelope(payload)

        # Assert
        assert result is True
        assert adapter._drop_count == {}


# ---------------------------------------------------------------------------
# T4 — _shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    """T4 — _shutdown drains then closes; does not call unsubscribe."""

    @pytest.mark.asyncio
    async def test_drain_called_before_close(self) -> None:
        """nc.drain() is awaited before nc.close()."""
        # Arrange
        adapter = _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )
        mock_nc = AsyncMock()
        call_order: list[str] = []
        mock_nc.drain = AsyncMock(side_effect=lambda: call_order.append("drain"))
        mock_nc.close = AsyncMock(side_effect=lambda: call_order.append("close"))
        adapter._nc = mock_nc

        # Act
        await adapter._shutdown()

        # Assert
        assert call_order == ["drain", "close"], (
            f"Expected drain then close, got: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_drain_and_close_each_called_once(self) -> None:
        """nc.drain() and nc.close() are each called exactly once."""
        # Arrange
        adapter = _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )
        mock_nc = AsyncMock()
        adapter._nc = mock_nc

        # Act
        await adapter._shutdown()

        # Assert
        mock_nc.drain.assert_awaited_once()
        mock_nc.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsubscribe_not_called(self) -> None:
        """nc.unsubscribe() is NOT called — drain subsumes subscription teardown."""
        # Arrange
        adapter = _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )
        mock_nc = AsyncMock()
        adapter._nc = mock_nc

        # Act
        await adapter._shutdown()

        # Assert — unsubscribe must never have been called
        mock_nc.unsubscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_skips_when_nc_is_none(self) -> None:
        """_shutdown is a no-op when _nc has not been set (pre-run state)."""
        # Arrange
        adapter = _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )
        # _nc is None by default

        # Act / Assert — no AttributeError raised
        await adapter._shutdown()


# ---------------------------------------------------------------------------
# T5 — health()
# ---------------------------------------------------------------------------


class TestHealth:
    """T5 — health() returns the correct shape and reflects live state."""

    def _make_adapter(self) -> _ConcreteAdapter:
        return _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=2,
        )

    def test_health_returns_all_required_keys(self) -> None:
        """health() returns a dict with all documented keys."""
        # Arrange
        adapter = self._make_adapter()

        # Act
        result = adapter.health()

        # Assert
        required_keys = {
            "status",
            "connected",
            "uptime_s",
            "subject",
            "queue_group",
            "schema_version",
        }
        assert required_keys.issubset(result.keys())

    def test_status_is_ok(self) -> None:
        """health() always reports status == 'ok'."""
        # Arrange
        adapter = self._make_adapter()

        # Act
        result = adapter.health()

        # Assert
        assert result["status"] == "ok"

    def test_connected_reflects_nc_is_connected_true(self) -> None:
        """connected key mirrors nc.is_connected when nc is set."""
        # Arrange
        adapter = self._make_adapter()
        mock_nc = MagicMock()
        mock_nc.is_connected = True
        adapter._nc = mock_nc

        # Act
        result = adapter.health()

        # Assert
        assert result["connected"] is True

    def test_connected_reflects_nc_is_connected_false(self) -> None:
        """connected is False when nc.is_connected is False."""
        # Arrange
        adapter = self._make_adapter()
        mock_nc = MagicMock()
        mock_nc.is_connected = False
        adapter._nc = mock_nc

        # Act
        result = adapter.health()

        # Assert
        assert result["connected"] is False

    def test_connected_is_false_when_nc_is_none(self) -> None:
        """connected is False when _nc has not been set (pre-run)."""
        # Arrange
        adapter = self._make_adapter()
        # _nc is None

        # Act
        result = adapter.health()

        # Assert
        assert result["connected"] is False

    def test_uptime_zero_before_run(self) -> None:
        """uptime_s is 0.0 before run() sets _started_at."""
        # Arrange
        adapter = self._make_adapter()
        # _started_at is None

        # Act
        result = adapter.health()

        # Assert
        assert result["uptime_s"] == 0.0

    def test_uptime_positive_after_started_at_set(self) -> None:
        """uptime_s is positive when _started_at was set in the past."""
        # Arrange
        adapter = self._make_adapter()
        adapter._started_at = time.monotonic() - 5.0  # 5 seconds ago

        # Act
        result = adapter.health()

        # Assert
        assert result["uptime_s"] > 0.0

    def test_subject_and_queue_group_in_health(self) -> None:
        """health() includes the adapter's subject and queue_group values."""
        # Arrange
        adapter = self._make_adapter()

        # Act
        result = adapter.health()

        # Assert
        assert result["subject"] == "lyra.inbound.telegram.main"
        assert result["queue_group"] == "telegram_workers"

    def test_schema_version_in_health(self) -> None:
        """health() includes the schema_version the adapter was built with."""
        # Arrange
        adapter = self._make_adapter()

        # Act
        result = adapter.health()

        # Assert
        assert result["schema_version"] == 2


# ---------------------------------------------------------------------------
# T6 — run()
# ---------------------------------------------------------------------------


class TestRun:
    """T6 — run() wires NATS, readiness check, subscription, and signal handlers."""

    def _make_adapter(self) -> _ConcreteAdapter:
        return _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )

    @pytest.mark.asyncio
    async def test_wait_ready_always_called(self) -> None:
        """_wait_ready() is called on every run() invocation."""
        # Arrange
        adapter = self._make_adapter()
        stop = asyncio.Event()
        stop.set()  # immediate exit

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.subscribe = AsyncMock()
        mock_nc.drain = AsyncMock()
        mock_nc.close = AsyncMock()

        with (
            patch(
                "roxabi_nats.adapter_base.nats_connect",
                new=AsyncMock(return_value=mock_nc),
            ),
            patch(
                "roxabi_nats.adapter_base.wait_for_hub",
                new=AsyncMock(return_value=True),
            ) as mock_wait,
        ):
            # Act
            await adapter.run("nats://localhost:4222", stop=stop)

        # Assert
        mock_wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_subscribe_called_with_subject_and_queue(self) -> None:
        """nc.subscribe is called with the adapter's subject and queue_group."""
        # Arrange
        adapter = self._make_adapter()
        stop = asyncio.Event()
        stop.set()

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.subscribe = AsyncMock()
        mock_nc.drain = AsyncMock()
        mock_nc.close = AsyncMock()

        with (
            patch(
                "roxabi_nats.adapter_base.nats_connect",
                new=AsyncMock(return_value=mock_nc),
            ),
            patch(
                "roxabi_nats.adapter_base.wait_for_hub",
                new=AsyncMock(return_value=True),
            ),
        ):
            # Act
            await adapter.run("nats://localhost:4222", stop=stop)

        # Assert
        mock_nc.subscribe.assert_awaited_once_with(
            "lyra.inbound.telegram.main",
            queue="telegram_workers",
            cb=adapter._dispatch,
        )

    @pytest.mark.asyncio
    async def test_signal_handlers_not_registered_when_stop_injected(self) -> None:
        """When stop event is pre-injected, loop.add_signal_handler is NOT called."""
        # Arrange
        adapter = self._make_adapter()
        stop = asyncio.Event()
        stop.set()

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.subscribe = AsyncMock()
        mock_nc.drain = AsyncMock()
        mock_nc.close = AsyncMock()

        with (
            patch(
                "roxabi_nats.adapter_base.nats_connect",
                new=AsyncMock(return_value=mock_nc),
            ),
            patch(
                "roxabi_nats.adapter_base.wait_for_hub",
                new=AsyncMock(return_value=True),
            ),
            patch("asyncio.get_running_loop") as mock_get_loop,
        ):
            # Act
            await adapter.run("nats://localhost:4222", stop=stop)

        # Assert — get_running_loop was never called (signal setup skipped)
        mock_get_loop.assert_not_called()

    @pytest.mark.asyncio
    async def test_signal_handlers_registered_when_stop_is_none(self) -> None:
        """When stop=None, loop.add_signal_handler is called for SIGTERM and SIGINT."""
        # Arrange
        adapter = self._make_adapter()

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.subscribe = AsyncMock()
        mock_nc.drain = AsyncMock()
        mock_nc.close = AsyncMock()

        # We need to control the stop event so run() exits cleanly.
        # Capture the event that run() creates internally by intercepting
        # add_signal_handler — on the second call, set the event so run() exits.
        from typing import Any

        captured_setters: list[Any] = []

        mock_loop = MagicMock()

        def _capture_handler(sig: signal.Signals, setter: object) -> None:
            captured_setters.append((sig, setter))
            # Trigger stop after both handlers are registered
            if len(captured_setters) == 2:
                # The setter is stop.set — call it so run() can exit
                for _, fn in captured_setters:
                    fn()

        mock_loop.add_signal_handler = MagicMock(side_effect=_capture_handler)

        with (
            patch(
                "roxabi_nats.adapter_base.nats_connect",
                new=AsyncMock(return_value=mock_nc),
            ),
            patch(
                "roxabi_nats.adapter_base.wait_for_hub",
                new=AsyncMock(return_value=True),
            ),
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            # Act
            await adapter.run("nats://localhost:4222", stop=None)

        # Assert — add_signal_handler called twice: once for SIGTERM, once for SIGINT
        assert mock_loop.add_signal_handler.call_count == 2
        registered_sigs = {
            c.args[0] for c in mock_loop.add_signal_handler.call_args_list
        }
        assert signal.SIGTERM in registered_sigs
        assert signal.SIGINT in registered_sigs

    @pytest.mark.asyncio
    async def test_started_at_set_during_run(self) -> None:
        """_started_at is set to a float after run() initialises."""
        # Arrange
        adapter = self._make_adapter()
        stop = asyncio.Event()
        stop.set()

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.subscribe = AsyncMock()
        mock_nc.drain = AsyncMock()
        mock_nc.close = AsyncMock()

        with (
            patch(
                "roxabi_nats.adapter_base.nats_connect",
                new=AsyncMock(return_value=mock_nc),
            ),
            patch(
                "roxabi_nats.adapter_base.wait_for_hub",
                new=AsyncMock(return_value=True),
            ),
        ):
            # Act
            await adapter.run("nats://localhost:4222", stop=stop)

        # Assert
        assert adapter._started_at is not None
        assert isinstance(adapter._started_at, float)

    @pytest.mark.asyncio
    async def test_shutdown_called_on_stop(self) -> None:
        """_shutdown (drain+close) is invoked when the stop event fires."""
        # Arrange
        adapter = self._make_adapter()
        stop = asyncio.Event()
        stop.set()

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.subscribe = AsyncMock()
        mock_nc.drain = AsyncMock()
        mock_nc.close = AsyncMock()

        with (
            patch(
                "roxabi_nats.adapter_base.nats_connect",
                new=AsyncMock(return_value=mock_nc),
            ),
            patch(
                "roxabi_nats.adapter_base.wait_for_hub",
                new=AsyncMock(return_value=True),
            ),
        ):
            # Act
            await adapter.run("nats://localhost:4222", stop=stop)

        # Assert — drain and close both called (shutdown path)
        mock_nc.drain.assert_awaited_once()
        mock_nc.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# T7 — _dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    """T7 — _dispatch routes messages correctly: JSON error, bad envelope, valid."""

    def _make_adapter(self) -> _ConcreteAdapter:
        return _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )

    @pytest.mark.asyncio
    async def test_malformed_json_does_not_call_handle(self) -> None:
        """Non-JSON bytes → handle() is never called."""
        # Arrange
        adapter = self._make_adapter()
        adapter.handle = AsyncMock()  # type: ignore[method-assign]
        msg = MagicMock()
        msg.data = b"not json at all"

        # Act
        await adapter._dispatch(msg)

        # Assert
        adapter.handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_envelope_does_not_call_handle(self) -> None:
        """Valid JSON but wrong schema_version → handle() is never called."""
        # Arrange
        adapter = self._make_adapter()
        adapter.handle = AsyncMock()  # type: ignore[method-assign]
        msg = MagicMock()
        msg.data = b'{"schema_version": 99, "text": "hello"}'

        # Act
        await adapter._dispatch(msg)

        # Assert
        adapter.handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_message_calls_handle_with_msg_and_payload(self) -> None:
        """Valid JSON + valid envelope → handle called with msg and parsed payload."""
        # Arrange
        adapter = self._make_adapter()
        adapter.handle = AsyncMock()  # type: ignore[method-assign]
        msg = MagicMock()
        raw_payload = {"schema_version": 1, "text": "hello"}
        msg.data = b'{"schema_version": 1, "text": "hello"}'

        # Act
        await adapter._dispatch(msg)

        # Assert
        adapter.handle.assert_awaited_once_with(msg, raw_payload)


# ---------------------------------------------------------------------------
# TestHeartbeatConstruction
# ---------------------------------------------------------------------------


class TestHeartbeatConstruction:
    """Heartbeat kwargs are stored; _worker_id is formatted correctly."""

    def _make_adapter(self, **kwargs) -> _ConcreteAdapter:
        return _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
            **kwargs,
        )

    def test_heartbeat_subject_stored(self) -> None:
        """heartbeat_subject kwarg is stored on the adapter."""
        # Arrange / Act
        adapter = self._make_adapter(heartbeat_subject="lyra.voice.stt.heartbeat")

        # Assert
        assert adapter._heartbeat_subject == "lyra.voice.stt.heartbeat"

    def test_heartbeat_interval_stored(self) -> None:
        """heartbeat_interval kwarg is stored (default 5.0)."""
        # Arrange / Act
        adapter_default = self._make_adapter()
        adapter_custom = self._make_adapter(heartbeat_interval=10.0)

        # Assert
        assert adapter_default._heartbeat_interval == 5.0
        assert adapter_custom._heartbeat_interval == 10.0

    def test_worker_id_format(self) -> None:
        """_worker_id is formatted as '{queue_group}-{hostname}-{pid}'."""
        import os
        import socket

        # Arrange / Act
        adapter = self._make_adapter()

        # Assert
        expected = f"telegram_workers-{socket.gethostname()}-{os.getpid()}"
        assert adapter._worker_id == expected

    def test_no_heartbeat_subject_stores_none(self) -> None:
        """heartbeat_subject=None (default) stores None, no task attr."""
        # Arrange / Act
        adapter = self._make_adapter()

        # Assert
        assert adapter._heartbeat_subject is None
        assert adapter._heartbeat_task is None

    def test_existing_callers_unaffected(self) -> None:
        """NatsAdapterBase subclass with no heartbeat kwargs still works."""
        # Arrange / Act — no heartbeat kwargs, must not raise
        adapter = _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )

        # Assert — basic fields are still intact
        assert adapter.subject == "lyra.inbound.telegram.main"
        assert adapter._heartbeat_subject is None


# ---------------------------------------------------------------------------
# TestHeartbeatLoop
# ---------------------------------------------------------------------------


class TestHeartbeatLoop:
    """_heartbeat_loop publishes, handles errors, and exits on disconnected state."""

    def _make_adapter(self, **kwargs) -> _ConcreteAdapter:
        return _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
            heartbeat_subject="lyra.voice.stt.heartbeat",
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_publishes_payload_at_interval(self) -> None:
        """_heartbeat_loop publishes heartbeat_payload() JSON to heartbeat_subject."""
        # Arrange
        adapter = self._make_adapter(heartbeat_interval=0.01)
        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.is_closed = False
        adapter._nc = mock_nc
        adapter._started_at = time.monotonic()

        publish_calls: list[tuple] = []

        async def _fake_publish(subject: str, data: bytes) -> None:
            publish_calls.append((subject, data))
            # Close after first publish to stop the loop
            mock_nc.is_closed = True

        mock_nc.publish = AsyncMock(side_effect=_fake_publish)

        # Act
        await adapter._heartbeat_loop()

        # Assert
        assert len(publish_calls) == 1
        subject, data = publish_calls[0]
        assert subject == "lyra.voice.stt.heartbeat"
        payload = json.loads(data)
        assert "worker_id" in payload
        assert "service" in payload
        assert "ts" in payload

    @pytest.mark.asyncio
    async def test_publish_error_logs_warning_and_continues(self) -> None:
        """Publish error logs a warning and loop continues (no crash)."""
        # Arrange
        adapter = self._make_adapter(heartbeat_interval=0.01)
        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.is_closed = False
        adapter._nc = mock_nc
        adapter._started_at = time.monotonic()

        call_count = 0

        async def _failing_publish(subject: str, data: bytes) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("publish failed")
            # Close after second call so loop exits
            mock_nc.is_closed = True

        mock_nc.publish = AsyncMock(side_effect=_failing_publish)

        # Act — must not raise despite publish error on first call
        with patch("roxabi_nats.adapter_base.log") as mock_log:
            await adapter._heartbeat_loop()

        # Assert — warning logged, loop continued to second call
        mock_log.warning.assert_called()
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_loop_exits_when_nc_none(self) -> None:
        """_heartbeat_loop exits immediately when _nc is None."""
        # Arrange
        adapter = self._make_adapter()
        adapter._nc = None  # no connection

        # Act — must return immediately without error
        await adapter._heartbeat_loop()

        # Assert — we just check it returned (no infinite loop / no error)

    @pytest.mark.asyncio
    async def test_loop_exits_when_nc_closed(self) -> None:
        """_heartbeat_loop exits when _nc.is_closed is True."""
        # Arrange
        adapter = self._make_adapter()
        mock_nc = AsyncMock()
        mock_nc.is_connected = False
        mock_nc.is_closed = True  # closed connection terminates the loop
        adapter._nc = mock_nc

        # Act — must return immediately without publishing
        await adapter._heartbeat_loop()

        # Assert
        mock_nc.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_sleeps_on_disconnect_then_resumes(self) -> None:
        """Loop skips publish while disconnected; resumes when reconnected."""
        # Arrange
        adapter = self._make_adapter(heartbeat_interval=0.01)
        mock_nc = AsyncMock()
        mock_nc.is_connected = False  # start disconnected
        mock_nc.is_closed = False
        adapter._nc = mock_nc
        adapter._started_at = time.monotonic()

        publish_calls: list = []

        async def _fake_publish(subject: str, data: bytes) -> None:
            publish_calls.append(subject)
            mock_nc.is_closed = True  # close after first publish to stop the loop

        mock_nc.publish = AsyncMock(side_effect=_fake_publish)

        async def _reconnect_after_sleep() -> None:
            await asyncio.sleep(0.05)
            mock_nc.is_connected = True

        # Act — reconnect happens after one sleep cycle
        await asyncio.gather(adapter._heartbeat_loop(), _reconnect_after_sleep())

        # Assert — loop published once after reconnect, not while disconnected
        assert len(publish_calls) == 1


# ---------------------------------------------------------------------------
# TestHeartbeatShutdown
# ---------------------------------------------------------------------------


class TestHeartbeatShutdown:
    """_shutdown() properly cancels the heartbeat task before draining."""

    def _make_adapter(self, **kwargs) -> _ConcreteAdapter:
        return _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
            heartbeat_subject="lyra.voice.stt.heartbeat",
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_heartbeat_task_cancelled_before_drain(self) -> None:
        """_shutdown() cancels _heartbeat_task before calling nc.drain()."""
        # Arrange
        adapter = self._make_adapter()
        mock_nc = AsyncMock()
        drain_called_after_cancel = []

        async def _tracking_drain() -> None:
            drain_called_after_cancel.append(task_cancelled)

        mock_nc.drain = AsyncMock(side_effect=_tracking_drain)
        mock_nc.close = AsyncMock()
        adapter._nc = mock_nc

        # Create a real task that sleeps forever (will be cancelled by _shutdown)
        async def _forever() -> None:
            await asyncio.sleep(9999)

        task = asyncio.create_task(_forever())
        adapter._heartbeat_task = task

        task_cancelled = False

        # Patch task.cancel to track cancellation order
        original_cancel = task.cancel

        def _tracking_cancel(*args, **kwargs):
            nonlocal task_cancelled
            task_cancelled = True
            return original_cancel(*args, **kwargs)

        task.cancel = _tracking_cancel  # type: ignore[method-assign]

        # Act
        await adapter._shutdown()

        # Assert — drain was called after task was cancelled
        assert drain_called_after_cancel == [True]

    @pytest.mark.asyncio
    async def test_no_crash_when_no_heartbeat_task(self) -> None:
        """_shutdown() works normally when no heartbeat task was created."""
        # Arrange
        adapter = _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
        )
        mock_nc = AsyncMock()
        adapter._nc = mock_nc

        # Act / Assert — no error raised
        await adapter._shutdown()
        mock_nc.drain.assert_awaited_once()
        mock_nc.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestHeartbeatRun
# ---------------------------------------------------------------------------


class TestHeartbeatRun:
    """run() creates / skips the heartbeat task based on heartbeat_subject."""

    def _make_adapter(self, **kwargs) -> _ConcreteAdapter:
        return _ConcreteAdapter(
            subject="lyra.inbound.telegram.main",
            queue_group="telegram_workers",
            envelope_name="InboundMessage",
            schema_version=1,
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_heartbeat_task_created_when_subject_set(self) -> None:
        """run() creates _heartbeat_task when heartbeat_subject is set."""
        # Arrange
        adapter = self._make_adapter(heartbeat_subject="lyra.voice.stt.heartbeat")
        stop = asyncio.Event()

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.subscribe = AsyncMock()
        mock_nc.drain = AsyncMock()
        mock_nc.close = AsyncMock()

        # Set stop immediately after heartbeat task would be created
        async def _set_stop_soon() -> None:
            await asyncio.sleep(0.01)
            stop.set()

        with (
            patch(
                "roxabi_nats.adapter_base.nats_connect",
                new=AsyncMock(return_value=mock_nc),
            ),
            patch(
                "roxabi_nats.adapter_base.wait_for_hub",
                new=AsyncMock(return_value=True),
            ),
        ):
            asyncio.create_task(_set_stop_soon())
            await adapter.run("nats://localhost:4222", stop=stop)

        # Assert
        assert adapter._heartbeat_task is not None

    @pytest.mark.asyncio
    async def test_no_heartbeat_task_when_subject_none(self) -> None:
        """run() does NOT create _heartbeat_task when heartbeat_subject is None."""
        # Arrange
        adapter = self._make_adapter()  # no heartbeat_subject
        stop = asyncio.Event()
        stop.set()

        mock_nc = AsyncMock()
        mock_nc.is_connected = True
        mock_nc.subscribe = AsyncMock()
        mock_nc.drain = AsyncMock()
        mock_nc.close = AsyncMock()

        with (
            patch(
                "roxabi_nats.adapter_base.nats_connect",
                new=AsyncMock(return_value=mock_nc),
            ),
            patch(
                "roxabi_nats.adapter_base.wait_for_hub",
                new=AsyncMock(return_value=True),
            ),
        ):
            await adapter.run("nats://localhost:4222", stop=stop)

        # Assert
        assert adapter._heartbeat_task is None


# ---------------------------------------------------------------------------
# T4 (issue #729) — type_registry construction-time validation
# ---------------------------------------------------------------------------

from roxabi_nats._serialize import _EMPTY_RESOLVER  # noqa: E402


def test_type_registry_fail_fast_invalid_module() -> None:
    """NatsAdapterBase raises ValueError at init when module is missing."""
    # Arrange — module "no.such.module" does not exist
    # Act / Assert
    with pytest.raises(ValueError, match="cannot import no.such.module"):
        _ConcreteAdapter(
            subject="test.subj",
            queue_group="test.qg",
            envelope_name="test",
            schema_version=1,
            type_registry=[("no.such.module", "X")],
        )


def test_type_registry_none_ok() -> None:
    """NatsAdapterBase with type_registry=None binds the empty resolver singleton."""
    # Arrange / Act
    adapter = _ConcreteAdapter(
        subject="test.subj",
        queue_group="test.qg",
        envelope_name="test",
        schema_version=1,
        type_registry=None,
    )

    # Assert
    assert adapter._resolver is _EMPTY_RESOLVER
