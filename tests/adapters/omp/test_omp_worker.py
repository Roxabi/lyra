"""Unit tests for factory.adapters.omp.omp_worker.OmpWorker.

Tests are fully offline: RpcBridge is injected as a mock, no NATS
connection is opened.

asyncio_mode = "auto" is configured project-wide in pyproject.toml.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from factory.adapters.omp.omp_worker import OmpWorker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JOB_ID = "job-abc123"
_JOB_NAME = "summarise"


def _make_bridge() -> MagicMock:
    bridge = MagicMock()
    bridge.register = AsyncMock()
    bridge.run = AsyncMock()
    bridge.publish_error = AsyncMock()
    bridge.aclose = AsyncMock()
    return bridge


def _make_worker(bridge: MagicMock | None = None) -> OmpWorker:
    return OmpWorker(bridge=bridge or _make_bridge())


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
            "payload": {"prompt": "summarise this text"},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-001",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        await worker.handle(msg=MagicMock(), payload=payload)
        bridge.run.assert_awaited_once_with(
            prompt="summarise this text",
            job_id=_JOB_ID,
        )

    async def test_handle_no_error_published_on_success(self) -> None:
        bridge = _make_bridge()
        worker = _make_worker(bridge)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {"prompt": "do something"},
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
            "payload": {"prompt": "hi"},
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
