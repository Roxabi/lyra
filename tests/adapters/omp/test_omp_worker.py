"""Unit tests for factory.adapters.omp.omp_worker.OmpWorker.

Tests are fully offline: RpcBridge is injected as a mock, no NATS
connection is opened.

asyncio_mode = "auto" is configured project-wide in pyproject.toml.
"""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from factory.adapters.omp.omp_worker import OmpWorker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JOB_ID = "job-abc123"
_JOB_NAME = "summarise"
_POOL_ID = "test-pool"


def _make_bridge() -> MagicMock:
    bridge = MagicMock()
    bridge.register = AsyncMock()
    bridge.run = AsyncMock()
    bridge.publish_error = AsyncMock()
    bridge.aclose = AsyncMock()
    return bridge


def _make_pool() -> MagicMock:
    mock_client = MagicMock()
    pool = MagicMock()
    pool.acquire = AsyncMock(return_value=mock_client)
    pool.aclose = AsyncMock()
    mock_entry = MagicMock()
    mock_entry.session_file = None
    pool._entries = {_POOL_ID: mock_entry}
    return pool


def _make_worker(
    bridge: MagicMock | None = None, pool: MagicMock | None = None
) -> OmpWorker:
    return OmpWorker(bridge=bridge or _make_bridge(), pool=pool or _make_pool())


# ---------------------------------------------------------------------------
# Construction / static invariants
# ---------------------------------------------------------------------------


class TestOmpWorkerInvariants:
    def test_wait_ready_false(self) -> None:
        """Worker semantics: hub readiness is not a pre-condition."""
        worker = _make_worker()
        assert worker._wait_ready_flag is False

    def test_heartbeat_subject(self) -> None:
        worker = _make_worker()
        assert worker._heartbeat_subject == "factory.omp.heartbeat"

    def test_heartbeat_interval(self) -> None:
        worker = _make_worker()
        assert worker._heartbeat_interval == 30.0

    def test_cmd_subject(self) -> None:
        worker = _make_worker()
        assert worker.subject == "factory.jobs.omp"

    def test_queue_group(self) -> None:
        worker = _make_worker()
        assert worker.queue_group == "omp-workers"


# ---------------------------------------------------------------------------
# heartbeat_payload
# ---------------------------------------------------------------------------


class TestHeartbeatPayload:
    def test_worker_field_is_omp(self) -> None:
        worker = _make_worker()
        payload = worker.heartbeat_payload()
        assert payload.get("worker") == "omp"

    def test_inherits_base_fields(self) -> None:
        worker = _make_worker()
        payload = worker.heartbeat_payload()
        # base includes at minimum a timestamp or similar; verify worker key added
        assert "worker" in payload


# ---------------------------------------------------------------------------
# handle() — happy path
# ---------------------------------------------------------------------------


class TestHandleHappyPath:
    async def test_handle_dispatches_to_bridge_run(self) -> None:
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {"prompt": "summarise this text", "pool_id": _POOL_ID},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-001",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        await worker.handle(msg=MagicMock(), payload=payload)
        bridge.run.assert_awaited_once_with(
            prompt="summarise this text",
            job_id=_JOB_ID,
            client=ANY,
            session_file=ANY,
        )

    async def test_handle_no_error_published_on_success(self) -> None:
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {"prompt": "do something", "pool_id": _POOL_ID},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-002",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        await worker.handle(msg=MagicMock(), payload=payload)
        bridge.publish_error.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle() — invalid envelope → publish_error, not raise
# ---------------------------------------------------------------------------


class TestHandleInvalidEnvelope:
    async def test_invalid_reply_to_publishes_error(self) -> None:
        """reply_to must be a valid NATS subject; invalid value → publish_error."""
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {"prompt": "hello"},
            "contract_version": "1",
            "reply_to": "invalid reply subject with spaces",  # not a valid subject
            "trace_id": "trace-003",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        # Must NOT raise — must call publish_error instead
        await worker.handle(msg=MagicMock(), payload=payload)
        bridge.publish_error.assert_awaited_once()

    async def test_missing_job_name_publishes_error(self) -> None:
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            # job_name intentionally omitted
            "payload": {"prompt": "hello"},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-004",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        await worker.handle(msg=MagicMock(), payload=payload)
        bridge.publish_error.assert_awaited_once()

    async def test_bridge_run_not_called_on_parse_failure(self) -> None:
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        await worker.handle(msg=MagicMock(), payload={"bad": "data"})
        bridge.run.assert_not_awaited()

    async def test_handle_empty_prompt_string_rejected(self) -> None:
        """Empty string prompt must publish_error and must NOT call bridge.run."""
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {"prompt": ""},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-empty-prompt",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        await worker.handle(msg=MagicMock(), payload=payload)
        bridge.publish_error.assert_awaited_once()
        bridge.run.assert_not_awaited()

    async def test_handle_missing_prompt_rejected(self) -> None:
        """Missing prompt key must publish_error and must NOT call bridge.run."""
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-missing-prompt",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        await worker.handle(msg=MagicMock(), payload=payload)
        bridge.publish_error.assert_awaited_once()
        bridge.run.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle() — bridge.run raises → publish_error, not raise
# ---------------------------------------------------------------------------


class TestHandleBridgeRunError:
    async def test_runtime_error_publishes_error(self) -> None:
        bridge = _make_bridge()
        bridge.run.side_effect = RuntimeError("boom")
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {"prompt": "hi", "pool_id": _POOL_ID},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-005",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        # Must NOT propagate exception
        await worker.handle(msg=MagicMock(), payload=payload)
        bridge.publish_error.assert_awaited_once()
        exc_arg = bridge.publish_error.await_args.args[1]
        assert isinstance(exc_arg, RuntimeError)

    async def test_error_publishes_correct_job_id(self) -> None:
        bridge = _make_bridge()
        bridge.run.side_effect = ValueError("oops")
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {"prompt": "hi"},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-006",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        await worker.handle(msg=MagicMock(), payload=payload)
        job_id_arg = bridge.publish_error.await_args.args[0]
        assert job_id_arg == _JOB_ID


# ---------------------------------------------------------------------------
# run() — bridge.register called with nc
# ---------------------------------------------------------------------------


class TestRun:
    async def test_run_registers_bridge_before_loop(self) -> None:
        bridge = _make_bridge()
        worker = _make_worker(bridge)

        call_order: list[str] = []

        async def mock_nats_connect(*a, **kw):  # noqa: ARG001
            call_order.append("connect")
            return AsyncMock()

        async def mock_register(nc):  # noqa: ARG001
            call_order.append("register")

        async def mock_run_embedded(nc, stop):
            call_order.append("run_embedded")
            stop.set()  # prevent blocking

        bridge.register.side_effect = mock_register

        with (
            patch(
                "factory.adapters.omp.omp_worker.nats_connect",
                side_effect=mock_nats_connect,
            ),
            patch.object(worker, "run_embedded", side_effect=mock_run_embedded),
        ):
            import asyncio

            stop = asyncio.Event()
            stop.set()
            await worker.run("nats://localhost:4222", stop=stop)

        # register must precede run_embedded
        assert call_order.index("register") < call_order.index("run_embedded")

    async def test_run_closes_bridge_after_loop(self) -> None:
        """aclose() must run after the loop, in order register < loop < aclose."""
        bridge = _make_bridge()
        worker = _make_worker(bridge)

        call_order: list[str] = []

        async def mock_nats_connect(*a, **kw):  # noqa: ARG001
            return AsyncMock()

        async def mock_register(nc):  # noqa: ARG001
            call_order.append("register")

        async def mock_run_embedded(nc, stop):  # noqa: ARG001
            call_order.append("run_embedded")

        async def mock_aclose():
            call_order.append("aclose")

        bridge.register.side_effect = mock_register
        bridge.aclose.side_effect = mock_aclose

        with (
            patch(
                "factory.adapters.omp.omp_worker.nats_connect",
                side_effect=mock_nats_connect,
            ),
            patch.object(worker, "run_embedded", side_effect=mock_run_embedded),
        ):
            stop = asyncio.Event()
            stop.set()
            await worker.run("nats://localhost:4222", stop=stop)

        assert call_order == ["register", "run_embedded", "aclose"]
        bridge.aclose.assert_awaited_once()

    async def test_run_closes_bridge_when_loop_raises(self) -> None:
        """aclose() runs via the finally block even when the loop raises."""
        bridge = _make_bridge()
        worker = _make_worker(bridge)

        async def mock_nats_connect(*a, **kw):  # noqa: ARG001
            return AsyncMock()

        async def boom(nc, stop):  # noqa: ARG001
            raise RuntimeError("loop crashed")

        with (
            patch(
                "factory.adapters.omp.omp_worker.nats_connect",
                side_effect=mock_nats_connect,
            ),
            patch.object(worker, "run_embedded", side_effect=boom),
        ):
            stop = asyncio.Event()
            stop.set()
            with pytest.raises(RuntimeError, match="loop crashed"):
                await worker.run("nats://localhost:4222", stop=stop)

        bridge.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# handle() — backward compatibility: old-style payload (prompt only)
# ---------------------------------------------------------------------------


class TestHandleBackwardCompat:
    async def test_handle_prompt_only_payload_backward_compat(self) -> None:
        """Old-style envelope with only {prompt} in payload (no model_cfg, no
        system_prompt) must not raise and must call bridge.run with the prompt."""
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {
                "prompt": "x",
                "pool_id": _POOL_ID,
            },  # old-style: no model_cfg, no system_prompt
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-bc-001",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        # Arrange: bridge.run succeeds (default AsyncMock)
        # Act
        await worker.handle(msg=MagicMock(), payload=payload)
        # Assert: must not raise, must call bridge.run with prompt="x"
        bridge.run.assert_awaited_once_with(
            prompt="x",
            job_id=_JOB_ID,
            client=ANY,
            session_file=ANY,
        )
        bridge.publish_error.assert_not_awaited()

    async def test_handle_reads_model_cfg_and_system_prompt(self) -> None:
        """Enriched payload (model_cfg + system_prompt) must not raise.

        bridge.run must still receive only prompt+job_id (V1: extra fields
        read but not applied).
        """
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {
                "prompt": "x",
                "pool_id": _POOL_ID,
                "model_cfg": {"backend": "omp-rpc", "model": "grok-4-fast"},
                "system_prompt": "be terse",
            },
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-bc-002",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        await worker.handle(msg=MagicMock(), payload=payload)
        # Extra fields accepted/read — bridge.run signature unchanged
        bridge.run.assert_awaited_once_with(
            prompt="x",
            job_id=_JOB_ID,
            client=ANY,
            session_file=ANY,
        )
        bridge.publish_error.assert_not_awaited()
