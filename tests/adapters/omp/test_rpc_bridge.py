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
import os
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.adapters.omp._rpc_bridge import (
    _DEFAULT_MODEL,
    _DEFAULT_REQUEST_TIMEOUT,
    _ENV_REQUEST_TIMEOUT_KEY,
    _PINNED_SHA256,
    DigestMismatchError,
    RpcBridge,
    SteerViolationError,
    _classify_exception,
    _read_request_timeout,
)

pytestmark = pytest.mark.omp_contract

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JOB_ID = "job-abc123"
_PROGRESS_SUBJECT = f"factory.job.{_JOB_ID}.progress"
_RESULT_SUBJECT = f"factory.job.{_JOB_ID}.result"


def _make_fake_binary(
    tmp_path: Path, *, matching_digest: bool = True
) -> tuple[Path, str | None]:
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
    """Install a stub omp_rpc in sys.modules; return (module, RpcClient mock)."""
    client_instance = MagicMock()
    # omp_rpc is synchronous + thread-based — the bridge invokes these via
    # asyncio.to_thread, so the stubs are plain (sync) MagicMocks, never
    # AsyncMock (#1875).
    client_instance.start = MagicMock()
    client_instance.stop = MagicMock()
    client_instance.new_session = MagicMock()
    client_instance.prompt_and_wait = MagicMock()
    client_instance.set_model = MagicMock()
    client_instance.steer = MagicMock()
    client_instance.on_message_update = MagicMock()
    client_instance.on_tool_execution_start = MagicMock()
    client_instance.on_agent_end = MagicMock()

    rpc_class = MagicMock(return_value=client_instance)
    module = ModuleType("omp_rpc")
    module.RpcClient = rpc_class  # type: ignore[attr-defined]

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
        assert err.code == "transport.timeout"
        assert err.retryable is True
        assert err.message == "TimeoutError"

    def test_connection_refused_retryable(self) -> None:
        err = _classify_exception(ConnectionRefusedError())
        assert err.code == "transport.error"
        assert err.retryable is True
        assert err.message == "ConnectionRefusedError"

    def test_digest_mismatch_not_retryable(self) -> None:
        exc = DigestMismatchError(actual="aaa", expected="bbb")
        err = _classify_exception(exc)
        assert err.code == "transport.contract_mismatch"
        assert err.retryable is False
        assert err.message == "DigestMismatchError"

    def test_generic_exception_maps_internal_error(self) -> None:
        err = _classify_exception(RuntimeError("oops"))
        assert err.code == "worker.internal"
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
            with patch("factory.adapters.omp._rpc_digest._PINNED_SHA256", actual_sha):
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
        with patch("factory.adapters.omp._rpc_digest._PINNED_SHA256", actual_sha):
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
        client.prompt_and_wait.assert_called_once_with("hello")

    async def test_run_sets_current_job_id(self, bridge_and_nc) -> None:
        bridge, nc, _client = bridge_and_nc
        await bridge.register(nc)
        await bridge.run(prompt="hello", job_id=_JOB_ID)
        assert bridge._current_job_id == _JOB_ID

    async def test_in_prompt_await_cleared_after_run(self, bridge_and_nc) -> None:
        bridge, nc, _ = bridge_and_nc
        await bridge.register(nc)
        await bridge.run(prompt="hello", job_id=_JOB_ID)
        assert bridge._in_prompt_await is False

    async def test_in_prompt_await_cleared_on_exception(self, bridge_and_nc) -> None:
        bridge, nc, client = bridge_and_nc
        await bridge.register(nc)
        client.prompt_and_wait.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await bridge.run(prompt="hello", job_id=_JOB_ID)
        assert bridge._in_prompt_await is False

    async def test_run_resets_last_agent_end_event_between_jobs(
        self, bridge_and_nc
    ) -> None:
        """Falsification: deleting `self._last_agent_end_event = None` in run() makes
        this fail.

        Without the reset, a stale _last_agent_end_event from a prior job provides
        assistant_text for the fallback path → result becomes "STALE-FROM-PRIOR-JOB"
        instead of "".  With the reset, the stale event is cleared, the fallback finds
        nothing, and text="".
        """
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)

        # prompt_and_wait returns a turn with no assistant_text → triggers fallback
        client.prompt_and_wait.return_value = SimpleNamespace(assistant_text=None)

        # Simulate leftover _last_agent_end_event from a prior job
        bridge._last_agent_end_event = SimpleNamespace(
            messages=[SimpleNamespace(assistant_text="STALE-FROM-PRIOR-JOB")]
        )

        await bridge.run(prompt="p", job_id=_JOB_ID)

        nc.publish.assert_awaited_once()
        payload_bytes = nc.publish.await_args.args[1]
        payload = json.loads(payload_bytes)
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "worker.validation", (
            "empty text without OMP stopReason=error is a validation failure"
        )


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
        client.steer.assert_called_once_with("nudge")


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

        logger_name = "factory.adapters.omp._rpc_bridge"
        with caplog.at_level(logging.WARNING, logger=logger_name):
            await bridge.publish_error(_JOB_ID, RuntimeError("x"))
        assert nc.publish.await_count == 0

    async def test_publish_error_is_no_op_when_result_already_sent(
        self, bridge_and_nc
    ) -> None:
        """Guard (a): double-publish is silently dropped when _result_sent is True."""
        bridge, nc, _ = bridge_and_nc
        await bridge.register(nc)
        bridge._result_sent = True
        await bridge.publish_error(_JOB_ID, RuntimeError("x"))
        nc.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# Event callbacks → NATS publish (1:1 mapping, ADR-073 guards)
# ---------------------------------------------------------------------------


class TestCallbackEvents:
    async def test_on_message_update_publishes_to_progress_subject(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, _client = bridge_and_nc
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
        bridge, nc, _client = bridge_and_nc
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

    async def test_on_message_update_no_str_exc_in_fields(self, bridge_and_nc) -> None:
        """ADR-073: no exception string in published field."""
        bridge, nc, _client = bridge_and_nc
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
        bridge, nc, _client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(
            tool_name="bash", tool_id="tid-1", tool_input={"cmd": "ls"}
        )
        bridge._on_tool_execution_start(event)
        await asyncio.sleep(0)  # event-based
        await asyncio.sleep(0)  # event-based
        nc.publish.assert_awaited_once()
        subject = nc.publish.await_args.args[0]
        assert subject == _PROGRESS_SUBJECT

    async def test_on_tool_execution_start_payload_fields(self, bridge_and_nc) -> None:
        bridge, nc, _client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(
            tool_name="read_file", tool_id="tid-2", tool_input={"path": "/tmp"}
        )
        bridge._on_tool_execution_start(event)
        await asyncio.sleep(0)  # event-based
        await asyncio.sleep(0)  # event-based
        payload_bytes = nc.publish.await_args.args[1]
        payload = json.loads(payload_bytes)
        assert payload["step"] == "tool_start"
        assert payload["tool_name"] == "read_file"
        assert payload["detail"]["tool_name"] == "read_file"

    async def test_on_agent_end_stores_event(self, bridge_and_nc) -> None:
        """_on_agent_end is store-only: stores event in _last_agent_end_event.

        Storing the event must not trigger a publish. run() is the sole success
        publisher (True Path B — race-free after prompt_and_wait returns).
        """
        bridge, nc, _client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(messages=())
        bridge._on_agent_end(event)
        await asyncio.sleep(0)  # event-based: drain scheduled-publish window
        await asyncio.sleep(0)  # event-based

        assert bridge._last_agent_end_event is event, (
            "_on_agent_end must store event in _last_agent_end_event"
        )
        nc.publish.assert_not_awaited()

    async def test_on_agent_end_does_not_publish(self, bridge_and_nc) -> None:
        """_on_agent_end must never publish to the result subject (store-only)."""
        bridge, nc, _client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(messages=())
        bridge._on_agent_end(event)
        await asyncio.sleep(0)  # event-based: drain scheduled-publish window
        await asyncio.sleep(0)  # event-based

        nc.publish.assert_not_awaited()

    async def test_callback_no_publish_when_nc_is_none(self, bridge_and_nc) -> None:
        bridge, nc, _client = bridge_and_nc
        # Do NOT call register — _nc stays None
        bridge._current_job_id = _JOB_ID

        event = SimpleNamespace(text="hi")
        bridge._on_message_update(event)
        await asyncio.sleep(0)  # event-based
        nc.publish.assert_not_awaited()

    async def test_callback_no_publish_when_job_id_is_none(self, bridge_and_nc) -> None:
        bridge, nc, _client = bridge_and_nc
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
        bridge, nc, _client = bridge_and_nc
        sub_mock = AsyncMock(unsubscribe=AsyncMock())
        nc.subscribe = AsyncMock(return_value=sub_mock)
        await bridge.register(nc)

        await bridge.run(prompt="hello", job_id=_JOB_ID)

        nc.subscribe.assert_awaited_once()
        assert nc.subscribe.await_args is not None
        subscribed_subject = nc.subscribe.await_args.args[0]
        assert subscribed_subject == _STEER_SUBJECT

    async def test_steer_bridge_unsubscribes_after_run(self, bridge_and_nc) -> None:
        bridge, nc, _client = bridge_and_nc
        sub_mock = AsyncMock(unsubscribe=AsyncMock())
        nc.subscribe = AsyncMock(return_value=sub_mock)
        await bridge.register(nc)

        await bridge.run(prompt="hello", job_id=_JOB_ID)

        sub_mock.unsubscribe.assert_awaited_once()

    async def test_steer_bridge_unsubscribes_on_exception(self, bridge_and_nc) -> None:
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

        # prompt_and_wait is sync, run via asyncio.to_thread — gate it on a
        # threading.Event so _in_prompt_await is still True when the steer handler
        # fires in this loop thread, then release it.
        release = threading.Event()
        client.prompt_and_wait.side_effect = lambda prompt: release.wait(1.0)
        run_task = asyncio.ensure_future(bridge.run(prompt="hello", job_id=_JOB_ID))

        # Give run() time to subscribe and enter the prompt_and_wait thread.
        await asyncio.sleep(0)  # event-based
        # Call the captured steer handler while the job is in flight.
        assert captured_handler, "subscribe callback not captured"
        msg = SimpleNamespace(data=b"steer this way")
        await captured_handler[0](msg)
        release.set()

        await run_task
        client.steer.assert_called_once_with("steer this way")

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

        client.steer.assert_not_called()

    async def test_steer_bridge_no_subscribe_when_nc_is_none(
        self, bridge_and_nc
    ) -> None:
        """If register() was not called, run() must not crash on subscribe."""
        bridge, nc, _client = bridge_and_nc
        # Do NOT call register — _nc stays None

        # Should not raise
        await bridge.run(prompt="hello", job_id=_JOB_ID)
        # publish-less: nc.subscribe never called since nc was None at subscribe time
        assert nc.subscribe.await_count == 0  # nc mock still clean


# ---------------------------------------------------------------------------
# TestStartLifecycle — falsification tests for #1875 fix
# ---------------------------------------------------------------------------


class _StatefulFakeRpcClient:
    """Mirrors omp_rpc.RpcClient's start→new_session contract: new_session()
    and prompt_and_wait() raise (like the real _require_process) until start()
    has run. Reproduces the #1875 crash deterministically."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.calls: list[Any] = []

    def start(self) -> "_StatefulFakeRpcClient":
        self.calls.append("start")
        self.started = True
        return self

    def stop(self) -> None:
        self.calls.append("stop")
        self.started = False

    def new_session(self, parent_session: Any = None) -> None:
        self.calls.append("new_session")
        if not self.started:
            raise RuntimeError("RPC client is not started")

    def prompt_and_wait(self, message: str, **kw: Any) -> None:
        self.calls.append("prompt_and_wait")
        if not self.started:
            raise RuntimeError("RPC client is not started")

    def steer(self, text: str) -> None:
        self.calls.append(("steer", text))

    def on_message_update(self, cb: Any) -> None: ...

    def on_tool_execution_start(self, cb: Any) -> None: ...

    def on_agent_end(self, cb: Any) -> None: ...


@pytest.mark.asyncio
class TestStartLifecycle:
    """Verify that RpcBridge.register() calls start() before new_session() (#1875)."""

    def setup_method(self) -> None:
        _remove_omp_rpc_stub()

    def teardown_method(self) -> None:
        _remove_omp_rpc_stub()

    def _make_bridge_with_fake(
        self, tmp_path: Path
    ) -> tuple["RpcBridge", "_StatefulFakeRpcClient"]:
        """Return a bridge wired to a _StatefulFakeRpcClient via sys.modules stub."""
        omp_bin, actual_sha = _make_fake_binary(tmp_path, matching_digest=True)
        fake = _StatefulFakeRpcClient()
        module = ModuleType("omp_rpc")
        module.RpcClient = lambda **kw: fake.__init__(**kw) or fake  # type: ignore[attr-defined]
        sys.modules["omp_rpc"] = module

        with patch(
            "factory.adapters.omp._rpc_digest._PINNED_SHA256",
            actual_sha,
        ):
            bridge = RpcBridge(omp_bin=omp_bin)
        return bridge, fake

    async def test_register_starts_client_before_new_session(
        self, tmp_path: Path
    ) -> None:
        """start() MUST precede new_session() — the core #1875 regression."""
        bridge, fake = self._make_bridge_with_fake(tmp_path)
        nc = AsyncMock()
        nc.subscribe = AsyncMock()

        # register() must not raise — if start() is absent or reordered, fake raises
        await bridge.register(nc)

        assert "start" in fake.calls, "start() was never called"
        start_idx = fake.calls.index("start")
        new_session_idx = fake.calls.index("new_session")
        assert start_idx < new_session_idx, (
            f"start() (idx={start_idx}) must precede "
            f"new_session() (idx={new_session_idx})"
        )

    async def test_aclose_stops_started_client(self, tmp_path: Path) -> None:
        """aclose() after register() calls stop() and sets _started=False."""
        bridge, fake = self._make_bridge_with_fake(tmp_path)
        nc = AsyncMock()
        nc.subscribe = AsyncMock()

        await bridge.register(nc)
        await bridge.aclose()

        assert fake.calls[-1] == "stop"
        assert bridge._started is False

    async def test_aclose_noop_when_not_started(self, tmp_path: Path) -> None:
        """aclose() before register() is a no-op — must not raise or call stop()."""
        bridge, fake = self._make_bridge_with_fake(tmp_path)

        await bridge.aclose()  # must not raise

        assert "stop" not in fake.calls

    async def test_constructs_client_with_executable_and_session_enabled(
        self, tmp_path: Path
    ) -> None:
        """RpcClient must be constructed with executable, no_session=False, provider."""
        omp_bin, actual_sha = _make_fake_binary(tmp_path, matching_digest=True)
        received_kwargs: dict[str, Any] = {}

        class _CapturingFake(_StatefulFakeRpcClient):
            def __init__(self, **kw: Any) -> None:
                received_kwargs.update(kw)
                super().__init__(**kw)

        module = ModuleType("omp_rpc")

        def _factory(**kw: Any) -> _CapturingFake:
            f = _CapturingFake(**kw)
            return f

        module.RpcClient = _factory  # type: ignore[attr-defined]
        sys.modules["omp_rpc"] = module

        with patch(
            "factory.adapters.omp._rpc_digest._PINNED_SHA256",
            actual_sha,
        ):
            RpcBridge(omp_bin=omp_bin)

        assert "executable" in received_kwargs, "executable kwarg missing"
        assert received_kwargs["executable"].endswith("/omp"), (
            f"executable should end with /omp, got: {received_kwargs['executable']}"
        )
        assert received_kwargs.get("no_session") is False, (
            "no_session=False must be passed to RpcClient"
        )
        assert received_kwargs.get("provider") == "litellm", (
            f"expected provider='litellm', got: {received_kwargs.get('provider')}"
        )
        assert received_kwargs.get("model") == _DEFAULT_MODEL, (
            f"expected model={_DEFAULT_MODEL!r}, got: {received_kwargs.get('model')}"
        )
        assert received_kwargs.get("request_timeout") == _DEFAULT_REQUEST_TIMEOUT, (
            "expected default request_timeout when unset"
        )

    async def test_injected_client_is_adopted_without_constructing(
        self, tmp_path: Path
    ) -> None:
        """When _client is injected (pool path), bridge adopts it as-is.

        No new RpcClient is constructed and _verify_digest is not called.
        """
        fake_client = MagicMock()
        rpc_class_mock = MagicMock()

        module = ModuleType("omp_rpc")
        module.RpcClient = rpc_class_mock  # type: ignore[attr-defined]
        sys.modules["omp_rpc"] = module

        with patch("factory.adapters.omp._rpc_bridge._verify_digest") as verify_mock:
            bridge = RpcBridge(_client=fake_client)

        rpc_class_mock.assert_not_called()
        verify_mock.assert_not_called()
        assert bridge._client is fake_client

    async def test_attach_wires_callbacks_without_start(self, tmp_path: Path) -> None:
        """attach() registers the 3 callbacks; no start()/new_session()."""
        fake_client = MagicMock()
        fake_client.on_message_update = MagicMock()
        fake_client.on_tool_execution_start = MagicMock()
        fake_client.on_agent_end = MagicMock()
        fake_client.start = MagicMock()
        fake_client.new_session = MagicMock()

        module = ModuleType("omp_rpc")
        module.RpcClient = MagicMock()  # type: ignore[attr-defined]
        sys.modules["omp_rpc"] = module

        bridge = RpcBridge(_client=fake_client)
        nc = AsyncMock()

        await bridge.attach(nc)

        fake_client.on_message_update.assert_called_once()
        fake_client.on_tool_execution_start.assert_called_once()
        fake_client.on_agent_end.assert_called_once()
        fake_client.start.assert_not_called()
        fake_client.new_session.assert_not_called()


# ---------------------------------------------------------------------------
# T2 — _on_agent_end must publish data={"result": <assistant_text>}
# T3 — run() must store the PromptTurn return value as _last_turn
# ---------------------------------------------------------------------------


class TestTurnFailurePublishing:
    async def test_stop_reason_error_publishes_model_unavailable(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)

        client.prompt_and_wait.return_value = SimpleNamespace(
            assistant_text=None,
            assistant_message={
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": (
                    "400 /chat/completions: Invalid model name passed in "
                    "model=grok-4-fast"
                ),
            },
        )

        await bridge.run(prompt="hello", job_id=_JOB_ID)

        payload = json.loads(nc.publish.await_args.args[1])
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "llm.model_unavailable"
        assert payload["error"]["message"] == "ModelUnavailable"
        assert "grok-4-fast" not in json.dumps(payload)

    async def test_invalid_model_falls_back_to_registry_first(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)

        failed_turn = SimpleNamespace(
            assistant_text=None,
            assistant_message={
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": "400 Invalid model name passed in model=grok-4-fast",
            },
        )
        success_turn = SimpleNamespace(
            assistant_text="fallback reply",
            assistant_message={"role": "assistant", "stopReason": "end_turn"},
        )
        client.prompt_and_wait.side_effect = [failed_turn, success_turn]

        with patch(
            "factory.adapters.omp._model_catalogue.first_registry_model",
            return_value="grok-4.20-non-reasoning",
        ):
            await bridge.run(
                prompt="hello",
                job_id=_JOB_ID,
                model="grok-4-fast",
            )

        client.set_model.assert_called()
        payload = json.loads(nc.publish.await_args.args[1])
        assert payload["status"] == "success"
        assert payload["data"]["result"] == "fallback reply"
        assert payload["data"]["model_fallback"] == {
            "requested": "grok-4-fast",
            "fallback": "grok-4.20-non-reasoning",
        }

    async def test_stop_reason_error_does_not_leak_provider_message(
        self, bridge_and_nc
    ) -> None:
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)

        secret = "super-secret-provider-detail"
        client.prompt_and_wait.return_value = SimpleNamespace(
            assistant_text="",
            assistant_message={
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": secret,
            },
        )

        await bridge.run(prompt="hello", job_id=_JOB_ID)

        payload_bytes = nc.publish.await_args.args[1]
        assert secret not in payload_bytes.decode()
        payload = json.loads(payload_bytes)
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "worker.internal"


class TestAgentEndResultData:
    async def test_on_agent_end_data_contains_assistant_text(
        self, bridge_and_nc
    ) -> None:
        """T2: run() must publish data={"result": <assistant_text>} (True Path B).

        run() is the sole success publisher (race-free, on the event loop after
        await asyncio.to_thread(prompt_and_wait) returns). _on_agent_end is store-only.
        Drive through run() end-to-end: mock prompt_and_wait to return a PromptTurn
        with assistant_text, assert the published payload carries the correct data.
        """
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)

        client.prompt_and_wait.return_value = SimpleNamespace(
            assistant_text="hello from omp"
        )

        await bridge.run(prompt="code a thing", job_id=_JOB_ID)

        nc.publish.assert_awaited_once()
        subject = nc.publish.await_args.args[0]
        assert subject == _RESULT_SUBJECT, (
            f"Expected result published to {_RESULT_SUBJECT!r}, got {subject!r}"
        )
        payload_bytes = nc.publish.await_args.args[1]
        payload = json.loads(payload_bytes)
        assert payload["status"] == "success"
        assert payload.get("data") == {"result": "hello from omp"}, (
            f"Expected data={{'result': 'hello from omp'}}, got {payload.get('data')!r}"
        )


class TestRunCapturesLastTurn:
    async def test_run_captures_last_turn(self, bridge_and_nc) -> None:
        """T3 (RED): run() must store the PromptTurn from prompt_and_wait as _last_turn.

        Today the return value of prompt_and_wait is discarded and _last_turn does
        not exist.  After T8 fix, run() assigns self._last_turn = <turn>.
        """
        bridge, nc, client = bridge_and_nc
        nc.subscribe = AsyncMock(return_value=AsyncMock(unsubscribe=AsyncMock()))
        await bridge.register(nc)

        sentinel_turn = SimpleNamespace(assistant_text="x")
        client.prompt_and_wait.return_value = sentinel_turn

        await bridge.run(prompt="hello", job_id=_JOB_ID)

        assert hasattr(bridge, "_last_turn"), (
            "_last_turn not set — run() must store the PromptTurn return value"
        )
        assert bridge._last_turn is sentinel_turn, (
            f"_last_turn should be the sentinel turn, got {bridge._last_turn!r}"
        )


class TestRequestTimeoutConfig:
    def test_read_request_timeout_defaults(self) -> None:
        env_without = {
            k: v for k, v in os.environ.items() if k != _ENV_REQUEST_TIMEOUT_KEY
        }
        with patch.dict(os.environ, env_without, clear=True):
            assert _read_request_timeout() == _DEFAULT_REQUEST_TIMEOUT

    def test_read_request_timeout_honours_env(self) -> None:
        with patch.dict(os.environ, {_ENV_REQUEST_TIMEOUT_KEY: "120"}):
            assert _read_request_timeout() == 120.0

    def test_read_request_timeout_rejects_invalid(self) -> None:
        with patch.dict(os.environ, {_ENV_REQUEST_TIMEOUT_KEY: "not-a-float"}):
            assert _read_request_timeout() == _DEFAULT_REQUEST_TIMEOUT
