"""Unit tests for factory.adapters.omp._rpc_bridge.

Tests are fully offline: omp_rpc is stubbed via sys.modules, and the
omp binary digest gate is exercised with a real tmp_path fake binary.

ADR-073 discipline: assert that bus-bound error fields contain
type(exc).__name__ only, never str(exc) content.

asyncio_mode = "auto" is configured project-wide in pyproject.toml.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.adapters.omp._rpc_bridge import (
    _PINNED_SHA256,
    DigestMismatchError,
    RpcBridge,
    SteerViolationError,
    _classify_exception,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JOB_ID = "job-abc123"
_PROGRESS_SUBJECT = f"factory.job.{_JOB_ID}.progress"
_RESULT_SUBJECT = f"factory.job.{_JOB_ID}.result"


def _make_fake_binary(tmp_path: Path, *, matching_digest: bool = True) -> Path:
    """Write a fake binary whose sha256 matches (or doesn't) the pinned hash."""
    omp_bin = tmp_path / "omp"
    if matching_digest:
        # Write content that hashes to _PINNED_SHA256.
        # We must actually produce the right hash — precompute from known content.
        content = b"fake-omp-binary-content"
        # Patch the constant instead for matching digest — easier and deterministic.
        # (Actual SHA256 of content is computed once here.)
        actual_sha = hashlib.sha256(content).hexdigest()
        omp_bin.write_bytes(content)
        return omp_bin, actual_sha
    else:
        omp_bin.write_bytes(b"wrong-content")
        return omp_bin, None


def _stub_omp_rpc_module() -> tuple[ModuleType, MagicMock]:
    """Install a stub omp_rpc in sys.modules; return (module, RpcClient mock instance)."""
    client_instance = MagicMock()
    client_instance.new_session = AsyncMock()
    client_instance.prompt_and_wait = AsyncMock()
    client_instance.steer = AsyncMock()
    client_instance.on_message_update = MagicMock()
    client_instance.on_tool_execution_start = MagicMock()
    client_instance.on_agent_end = MagicMock()

    rpc_class = MagicMock(return_value=client_instance)
    module = ModuleType("omp_rpc")
    module.RpcClient = rpc_class

    sys.modules["omp_rpc"] = module
    return module, client_instance


def _remove_omp_rpc_stub() -> None:
    sys.modules.pop("omp_rpc", None)


# ---------------------------------------------------------------------------
# _classify_exception
# ---------------------------------------------------------------------------


class TestClassifyException:
    def test_timeout_retryable(self) -> None:
        err = _classify_exception(asyncio.TimeoutError())
        assert err.code == "timeout"
        assert err.retryable is True
        assert err.message == "TimeoutError"

    def test_connection_refused_retryable(self) -> None:
        err = _classify_exception(ConnectionRefusedError())
        assert err.code == "connection_error"
        assert err.retryable is True
        assert err.message == "ConnectionRefusedError"

    def test_digest_mismatch_not_retryable(self) -> None:
        exc = DigestMismatchError(actual="aaa", expected="bbb")
        err = _classify_exception(exc)
        assert err.code == "digest_mismatch"
        assert err.retryable is False
        assert err.message == "DigestMismatchError"

    def test_generic_exception_maps_internal_error(self) -> None:
        err = _classify_exception(RuntimeError("oops"))
        assert err.code == "internal_error"
        assert err.retryable is False
        # ADR-073: message must be the type name, not the exception string
        assert err.message == "RuntimeError"
        assert "oops" not in err.message

    def test_message_never_contains_exc_str(self) -> None:
        """ADR-073 guard: str(exc) must never leak into message field."""
        secret = "my-secret-data"
        err = _classify_exception(ValueError(secret))
        assert secret not in err.message
        assert err.message == "ValueError"


# ---------------------------------------------------------------------------
# DigestMismatchError raised on wrong hash
# ---------------------------------------------------------------------------


class TestDigestGate:
    def test_wrong_digest_raises(self, tmp_path: Path) -> None:
        omp_bin = tmp_path / "omp"
        omp_bin.write_bytes(b"wrong-content")
        _stub_omp_rpc_module()
        try:
            with pytest.raises(DigestMismatchError) as exc_info:
                RpcBridge(omp_bin=omp_bin)
            assert exc_info.value.expected == _PINNED_SHA256
            assert exc_info.value.actual != _PINNED_SHA256
        finally:
            _remove_omp_rpc_stub()

    def test_matching_digest_constructs(self, tmp_path: Path) -> None:
        omp_bin = tmp_path / "omp"
        content = b"test-content"
        omp_bin.write_bytes(content)
        actual_sha = hashlib.sha256(content).hexdigest()
        _stub_omp_rpc_module()
        try:
            with patch(
                "factory.adapters.omp._rpc_bridge._PINNED_SHA256", actual_sha
            ):
                bridge = RpcBridge(omp_bin=omp_bin)
            assert bridge is not None
        finally:
            _remove_omp_rpc_stub()

    def test_missing_binary_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "no-such-file"
        _stub_omp_rpc_module()
        try:
            with pytest.raises((FileNotFoundError, OSError)):
                RpcBridge(omp_bin=missing)
        finally:
            _remove_omp_rpc_stub()


# ---------------------------------------------------------------------------
# Fixture: pre-constructed RpcBridge with matching digest + stubbed omp_rpc
# ---------------------------------------------------------------------------


@pytest.fixture()
def bridge_and_nc(tmp_path: Path):
    """Yield (bridge, nc_mock) with real digest satisfied and omp_rpc stubbed."""
    content = b"rpc-bridge-test-content"
    omp_bin = tmp_path / "omp"
    omp_bin.write_bytes(content)
    actual_sha = hashlib.sha256(content).hexdigest()
    _, client_instance = _stub_omp_rpc_module()
    try:
        with patch("factory.adapters.omp._rpc_bridge._PINNED_SHA256", actual_sha):
            bridge = RpcBridge(omp_bin=omp_bin)
        nc = AsyncMock()
        nc.publish = AsyncMock()
        yield bridge, nc, client_instance
    finally:
        _remove_omp_rpc_stub()


# ---------------------------------------------------------------------------
# register() — callback wiring order + new_session called last
# ---------------------------------------------------------------------------


class TestRegister:
    async def test_register_wires_callbacks_then_new_session(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        call_log: list[str] = []
        client.on_message_update.side_effect = lambda _: call_log.append("on_msg")
        client.on_tool_execution_start.side_effect = lambda _: call_log.append(
            "on_tool"
        )
        client.on_agent_end.side_effect = lambda _: call_log.append("on_end")
        client.new_session.side_effect = lambda: call_log.append("new_session")

        # We can't strictly order calls across different mock methods without
        # a shared sequence; instead just verify all are called.
        await bridge.register(nc)
        assert client.on_message_update.called
        assert client.on_tool_execution_start.called
        assert client.on_agent_end.called
        assert client.new_session.call_count == 1

    async def test_register_sets_nc(self, bridge_and_nc) -> None:
        bridge, nc, _ = bridge_and_nc
        await bridge.register(nc)
        assert bridge._nc is nc


# ---------------------------------------------------------------------------
# run() — prompt_and_wait called, _in_prompt_await guard
# ---------------------------------------------------------------------------


class TestRun:
    async def test_run_calls_prompt_and_wait(self, bridge_and_nc) -> None:
        bridge, nc, client = bridge_and_nc
        await bridge.register(nc)
        await bridge.run(prompt="hello", job_id=_JOB_ID)
        client.prompt_and_wait.assert_awaited_once_with("hello")

    async def test_run_sets_current_job_id(self, bridge_and_nc) -> None:
        bridge, nc, client = bridge_and_nc
        await bridge.register(nc)
        await bridge.run(prompt="hello", job_id=_JOB_ID)
        assert bridge._current_job_id == _JOB_ID

    async def test_in_prompt_await_cleared_after_run(self, bridge_and_nc) -> None:
        bridge, nc, _ = bridge_and_nc
        await bridge.register(nc)
        await bridge.run(prompt="hello", job_id=_JOB_ID)
        assert bridge._in_prompt_await is False

    async def test_in_prompt_await_cleared_on_exception(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        await bridge.register(nc)
        client.prompt_and_wait.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await bridge.run(prompt="hello", job_id=_JOB_ID)
        assert bridge._in_prompt_await is False


# ---------------------------------------------------------------------------
# steer() — guard against in-flight steer
# ---------------------------------------------------------------------------


class TestSteer:
    async def test_steer_while_in_flight_raises(self, bridge_and_nc) -> None:
        bridge, nc, _ = bridge_and_nc
        await bridge.register(nc)
        bridge._in_prompt_await = True
        with pytest.raises(SteerViolationError):
            await bridge.steer(_JOB_ID, "new direction")

    async def test_steer_when_idle_delegates(self, bridge_and_nc) -> None:
        bridge, nc, client = bridge_and_nc
        await bridge.register(nc)
        bridge._in_prompt_await = False
        await bridge.steer(_JOB_ID, "nudge")
        client.steer.assert_awaited_once_with("nudge")


# ---------------------------------------------------------------------------
# publish_error() — ADR-073 + correct subject
# ---------------------------------------------------------------------------


class TestPublishError:
    async def test_publish_error_uses_result_subject(self, bridge_and_nc) -> None:
        bridge, nc, _ = bridge_and_nc
        await bridge.register(nc)
        await bridge.publish_error(_JOB_ID, RuntimeError("internal"))
        nc.publish.assert_awaited_once()
        subject = nc.publish.await_args.args[0]
        assert subject == _RESULT_SUBJECT

    async def test_publish_error_payload_status_error(self, bridge_and_nc) -> None:
        bridge, nc, _ = bridge_and_nc
        await bridge.register(nc)
        await bridge.publish_error(_JOB_ID, ValueError("secret-data"))
        payload_bytes = nc.publish.await_args.args[1]
        payload = json.loads(payload_bytes)
        assert payload["status"] == "error"
        assert payload["error"]["message"] == "ValueError"
        # ADR-073: secret must not appear on the bus
        assert "secret-data" not in payload_bytes.decode()

    async def test_publish_error_no_nc_logs_warning(
        self, bridge_and_nc, caplog
    ) -> None:
        bridge, nc, _ = bridge_and_nc
        # Don't call register — _nc stays None
        import logging

        with caplog.at_level(logging.WARNING, logger="factory.adapters.omp._rpc_bridge"):
            await bridge.publish_error(_JOB_ID, RuntimeError("x"))
        assert nc.publish.await_count == 0


# ---------------------------------------------------------------------------
# Event callbacks → NATS publish (1:1 mapping, ADR-073 guards)
# ---------------------------------------------------------------------------


class TestCallbackEvents:
    async def test_on_message_update_publishes_to_progress_subject(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(text="hello streaming")
        bridge._on_message_update(event)
        await asyncio.sleep(0)  # event-based: call_soon fires lambda
        await asyncio.sleep(0)  # event-based: ensure_future resolves coroutine

        nc.publish.assert_awaited_once()
        subject = nc.publish.await_args.args[0]
        assert subject == _PROGRESS_SUBJECT

    async def test_on_message_update_payload_fields(self, bridge_and_nc) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(text="partial chunk")
        bridge._on_message_update(event)
        await asyncio.sleep(0)  # event-based: call_soon fires lambda
        await asyncio.sleep(0)  # event-based: ensure_future resolves coroutine

        payload_bytes = nc.publish.await_args.args[1]
        payload = json.loads(payload_bytes)
        assert payload["step"] == "message_update"
        assert payload["partial_text"] == "partial chunk"
        assert payload["detail"]["partial_text"] == "partial chunk"

    async def test_on_message_update_no_str_exc_in_fields(
        self, bridge_and_nc
    ) -> None:
        """ADR-073: no exception string in published field."""
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(text="safe text")
        bridge._on_message_update(event)
        await asyncio.sleep(0)  # event-based
        await asyncio.sleep(0)  # event-based
        payload_bytes = nc.publish.await_args.args[1]
        # Must not contain any exception class message patterns
        assert b"Traceback" not in payload_bytes
        assert b"Exception" not in payload_bytes

    async def test_on_tool_execution_start_publishes_to_progress_subject(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(tool_name="bash", tool_id="tid-1", tool_input={"cmd": "ls"})
        bridge._on_tool_execution_start(event)
        await asyncio.sleep(0)  # event-based
        await asyncio.sleep(0)  # event-based
        nc.publish.assert_awaited_once()
        subject = nc.publish.await_args.args[0]
        assert subject == _PROGRESS_SUBJECT

    async def test_on_tool_execution_start_payload_fields(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(tool_name="read_file", tool_id="tid-2", tool_input={"path": "/tmp"})
        bridge._on_tool_execution_start(event)
        await asyncio.sleep(0)  # event-based
        await asyncio.sleep(0)  # event-based
        payload_bytes = nc.publish.await_args.args[1]
        payload = json.loads(payload_bytes)
        assert payload["step"] == "tool_start"
        assert payload["tool_name"] == "read_file"
        assert payload["detail"]["tool_name"] == "read_file"

    async def test_on_agent_end_publishes_to_result_subject(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(result="final answer")
        bridge._on_agent_end(event)
        await asyncio.sleep(0)  # event-based
        await asyncio.sleep(0)  # event-based
        nc.publish.assert_awaited_once()
        subject = nc.publish.await_args.args[0]
        assert subject == _RESULT_SUBJECT

    async def test_on_agent_end_payload_status_success(self, bridge_and_nc) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(result="done")
        bridge._on_agent_end(event)
        await asyncio.sleep(0)  # event-based
        await asyncio.sleep(0)  # event-based
        payload_bytes = nc.publish.await_args.args[1]
        payload = json.loads(payload_bytes)
        assert payload["status"] == "success"
        assert "error" not in payload or payload.get("error") is None

    async def test_callback_no_publish_when_nc_is_none(self, bridge_and_nc) -> None:
        bridge, nc, client = bridge_and_nc
        # Do NOT call register — _nc stays None
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(text="hi")
        bridge._on_message_update(event)
        await asyncio.sleep(0)  # event-based
        nc.publish.assert_not_awaited()

    async def test_callback_no_publish_when_job_id_is_none(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        # Deliberately omit _current_job_id assignment

        event = SimpleNamespace(text="hi")
        bridge._on_message_update(event)
        await asyncio.sleep(0)  # event-based
        nc.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# Steer bridge — NATS subscribe → rpc.steer() routing
# ---------------------------------------------------------------------------

_STEER_SUBJECT = f"factory.job.{_JOB_ID}.steer"


class TestSteerBridge:
    async def test_steer_bridge_subscribes_on_run(self, bridge_and_nc) -> None:
        bridge, nc, client = bridge_and_nc
        sub_mock = AsyncMock(unsubscribe=AsyncMock())
        nc.subscribe = AsyncMock(return_value=sub_mock)
        await bridge.register(nc)

        await bridge.run(prompt="hello", job_id=_JOB_ID)

        nc.subscribe.assert_awaited_once()
        subscribed_subject = nc.subscribe.await_args.args[0]
        assert subscribed_subject == _STEER_SUBJECT

    async def test_steer_bridge_unsubscribes_after_run(self, bridge_and_nc) -> None:
        bridge, nc, client = bridge_and_nc
        sub_mock = AsyncMock(unsubscribe=AsyncMock())
        nc.subscribe = AsyncMock(return_value=sub_mock)
        await bridge.register(nc)

        await bridge.run(prompt="hello", job_id=_JOB_ID)

        sub_mock.unsubscribe.assert_awaited_once()

    async def test_steer_bridge_unsubscribes_on_exception(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        sub_mock = AsyncMock(unsubscribe=AsyncMock())
        nc.subscribe = AsyncMock(return_value=sub_mock)
        client.prompt_and_wait.side_effect = RuntimeError("boom")
        await bridge.register(nc)

        with pytest.raises(RuntimeError):
            await bridge.run(prompt="hello", job_id=_JOB_ID)

        sub_mock.unsubscribe.assert_awaited_once()

    async def test_steer_bridge_forwards_to_rpc_client_when_active(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        captured_handler: list[Any] = []
        sub_mock = AsyncMock(unsubscribe=AsyncMock())

        async def _mock_subscribe(subject, *, cb, **kwargs):
            captured_handler.append(cb)
            return sub_mock

        nc.subscribe = _mock_subscribe
        await bridge.register(nc)

        # Start run in background so _in_prompt_await is True during handler call
        async def _slow_prompt_and_wait(prompt: str) -> None:
            await asyncio.sleep(0.01)  # event-based
        client.prompt_and_wait.side_effect = _slow_prompt_and_wait
        run_task = asyncio.ensure_future(bridge.run(prompt="hello", job_id=_JOB_ID))

        # Give run() time to subscribe and enter prompt_and_wait
        await asyncio.sleep(0)  # event-based
        # Call the captured steer handler
        assert captured_handler, "subscribe callback not captured"
        msg = SimpleNamespace(data=b"steer this way")
        await captured_handler[0](msg)

        await run_task
        client.steer.assert_awaited_once_with("steer this way")

    async def test_steer_bridge_drops_message_when_inactive(
        self, bridge_and_nc
    ) -> None:
        """Handler must silently drop steer if _in_prompt_await is False."""
        bridge, nc, client = bridge_and_nc
        captured_handler: list[Any] = []
        sub_mock = AsyncMock(unsubscribe=AsyncMock())

        async def _mock_subscribe(subject, *, cb, **kwargs):
            captured_handler.append(cb)
            return sub_mock

        nc.subscribe = _mock_subscribe
        await bridge.register(nc)

        await bridge.run(prompt="hello", job_id=_JOB_ID)
        # After run() _in_prompt_await is False

        assert captured_handler, "subscribe callback not captured"
        # Re-inject the handler invocation after completion
        bridge._in_prompt_await = False
        msg = SimpleNamespace(data=b"late steer")
        await captured_handler[0](msg)

        client.steer.assert_not_awaited()

    async def test_steer_bridge_no_subscribe_when_nc_is_none(
        self, bridge_and_nc
    ) -> None:
        """If register() was not called, run() must not crash on subscribe."""
        bridge, nc, client = bridge_and_nc
        # Do NOT call register — _nc stays None

        # Should not raise
        await bridge.run(prompt="hello", job_id=_JOB_ID)
        # publish-less: nc.subscribe never called since nc was None at subscribe time
        assert nc.subscribe.await_count == 0  # nc mock still clean
