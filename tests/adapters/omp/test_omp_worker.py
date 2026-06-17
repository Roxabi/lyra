"""Unit tests for factory.adapters.omp.omp_worker.OmpWorker (Model B).

Model B: OmpWorker(pool=...) — no bridge= parameter.
handle() spawns asyncio.Task per job (returns immediately).
_run_job() acquires from pool, calls worker.bridge.run, releases.
publish_job_error is a module-level function (patched at omp_worker namespace).

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

_VALID_PAYLOAD = {
    "job_id": _JOB_ID,
    "job_name": _JOB_NAME,
    "payload": {"prompt": "summarise this text", "pool_id": _POOL_ID},
    "contract_version": "1",
    "reply_to": "_INBOX.test.reply",
    "trace_id": "trace-001",
    "issued_at": "2026-06-11T00:00:00Z",
}


def _make_fake_pool_worker() -> MagicMock:
    """Fake _PoolWorker: has .bridge (with async run) and .session_file."""
    pw = MagicMock()
    pw.session_file = None
    pw.bridge = MagicMock()
    pw.bridge.run = AsyncMock()
    return pw


def _make_pool() -> MagicMock:
    """Pool fake with register/acquire/release/aclose."""
    fake_pool_worker = _make_fake_pool_worker()
    pool = MagicMock()
    pool.register = AsyncMock()
    pool.acquire = AsyncMock(return_value=fake_pool_worker)
    pool.release = MagicMock()
    pool.aclose = AsyncMock()
    return pool


def _make_worker(pool: MagicMock | None = None) -> OmpWorker:
    return OmpWorker(pool=pool or _make_pool())


async def _drain(worker: OmpWorker) -> None:
    """Wait for all spawned jobs to complete."""
    if worker._jobs:
        await asyncio.gather(*list(worker._jobs), return_exceptions=True)


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
        assert "worker" in payload


# ---------------------------------------------------------------------------
# handle() — happy path (spawn semantics)
# ---------------------------------------------------------------------------


class TestHandleHappyPath:
    async def test_handle_dispatches_to_bridge_run(self) -> None:
        pool = _make_pool()
        worker = _make_worker(pool)
        fake_pw = pool.acquire.return_value

        await worker.handle(msg=MagicMock(), payload=_VALID_PAYLOAD)
        await _drain(worker)

        pool.acquire.assert_awaited_once()
        fake_pw.bridge.run.assert_awaited_once_with(
            "summarise this text",
            _JOB_ID,
            session_file=fake_pw.session_file,
        )

    async def test_handle_releases_pool_worker_on_success(self) -> None:
        pool = _make_pool()
        worker = _make_worker(pool)
        fake_pw = pool.acquire.return_value

        await worker.handle(msg=MagicMock(), payload=_VALID_PAYLOAD)
        await _drain(worker)

        pool.release.assert_called_once_with(fake_pw)

    async def test_handle_no_error_published_on_success(self) -> None:
        pool = _make_pool()
        worker = _make_worker(pool)

        with patch(
            "factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()
        ) as mock_pje:
            await worker.handle(msg=MagicMock(), payload=_VALID_PAYLOAD)
            await _drain(worker)
            mock_pje.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle() — spawn returns immediately (non-blocking)
# ---------------------------------------------------------------------------


class TestHandleSpawnSemantics:
    async def test_handle_returns_before_job_completes(self) -> None:
        """handle() spawns and returns; it does NOT block on bridge.run."""
        pool = _make_pool()
        fake_pw = pool.acquire.return_value
        release_job = asyncio.Event()

        async def blocked_run(prompt, job_id, *, session_file=None):  # noqa: ARG001
            await release_job.wait()  # event-based: job hangs until the test frees it

        fake_pw.bridge.run.side_effect = blocked_run

        worker = _make_worker(pool)
        await worker.handle(msg=MagicMock(), payload=_VALID_PAYLOAD)

        # handle() returned while the job is still in flight — the spawned task is
        # pending (not done) precisely because release_job is unset. Deterministic
        # proof that handle() did not block on bridge.run; no wall-clock heuristic.
        assert len(worker._jobs) == 1
        (job_task,) = worker._jobs
        assert not job_task.done(), "handle() blocked until the job finished"

        release_job.set()
        await _drain(worker)

    async def test_two_jobs_dispatch_in_parallel(self) -> None:
        """Two handle() calls run their jobs concurrently, not serially."""
        # Each job signals on `started` when it enters bridge.run, then blocks on
        # `release`. If dispatch were serial, job 2 could not enter bridge.run until
        # job 1 returned — so both `started` signals arriving before any release is a
        # deterministic proof of concurrency (no wall-clock timing).
        started = asyncio.Semaphore(0)
        release = asyncio.Event()
        call_count = 0

        def _fresh_pool_worker() -> MagicMock:
            pw = _make_fake_pool_worker()

            async def run_until_released(prompt, job_id, *, session_file=None):  # noqa: ARG001
                nonlocal call_count
                call_count += 1
                started.release()  # this job has entered bridge.run
                await release.wait()  # event-based: hold until both are in flight

            pw.bridge.run.side_effect = run_until_released
            return pw

        pool = MagicMock()
        pool.register = AsyncMock()
        pool.release = MagicMock()
        pool.aclose = AsyncMock()
        # Each acquire call returns a fresh worker
        pool.acquire = AsyncMock(side_effect=lambda *_: _fresh_pool_worker())

        worker = _make_worker(pool)
        payload2 = {**_VALID_PAYLOAD, "job_id": "job-xyz789", "trace_id": "trace-002"}

        await worker.handle(msg=MagicMock(), payload=_VALID_PAYLOAD)
        await worker.handle(msg=MagicMock(), payload=payload2)

        # Both jobs must be in bridge.run simultaneously (would hang if serial).
        await asyncio.wait_for(started.acquire(), timeout=1.0)
        await asyncio.wait_for(started.acquire(), timeout=1.0)
        assert call_count == 2

        release.set()
        await _drain(worker)
        assert pool.release.call_count == 2


# ---------------------------------------------------------------------------
# handle() — invalid envelope → publish_job_error, not raise
# ---------------------------------------------------------------------------


class TestHandleInvalidEnvelope:
    async def test_invalid_reply_to_publishes_error(self) -> None:
        """reply_to with invalid NATS subject → publish_job_error (¬raise)."""
        worker = _make_worker()
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {"prompt": "hello"},
            "contract_version": "1",
            "reply_to": "invalid reply subject with spaces",
            "trace_id": "trace-003",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        with patch(
            "factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()
        ) as mock_pje:
            await worker.handle(msg=MagicMock(), payload=payload)
            mock_pje.assert_awaited_once()

    async def test_missing_job_name_publishes_error(self) -> None:
        worker = _make_worker()
        payload = {
            "job_id": _JOB_ID,
            # job_name intentionally omitted
            "payload": {"prompt": "hello"},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-004",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        with patch(
            "factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()
        ) as mock_pje:
            await worker.handle(msg=MagicMock(), payload=payload)
            mock_pje.assert_awaited_once()

    async def test_bridge_run_not_called_on_parse_failure(self) -> None:
        pool = _make_pool()
        worker = _make_worker(pool)
        with patch("factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()):
            await worker.handle(msg=MagicMock(), payload={"bad": "data"})
        pool.acquire.assert_not_awaited()

    async def test_handle_empty_prompt_string_rejected(self) -> None:
        """Empty string prompt → publish_job_error; bridge.run NOT called."""
        pool = _make_pool()
        worker = _make_worker(pool)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {"prompt": ""},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-empty-prompt",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        with patch(
            "factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()
        ) as mock_pje:
            await worker.handle(msg=MagicMock(), payload=payload)
            mock_pje.assert_awaited_once()
        pool.acquire.assert_not_awaited()

    async def test_handle_missing_prompt_rejected(self) -> None:
        """Missing prompt key → publish_job_error; bridge.run NOT called."""
        pool = _make_pool()
        worker = _make_worker(pool)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {},
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-missing-prompt",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        with patch(
            "factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()
        ) as mock_pje:
            await worker.handle(msg=MagicMock(), payload=payload)
            mock_pje.assert_awaited_once()
        pool.acquire.assert_not_awaited()


# ---------------------------------------------------------------------------
# _run_job() — bridge.run raises → publish_job_error, pool.release called
# ---------------------------------------------------------------------------


class TestHandleBridgeRunError:
    async def test_runtime_error_publishes_error(self) -> None:
        pool = _make_pool()
        fake_pw = pool.acquire.return_value
        fake_pw.bridge.run.side_effect = RuntimeError("boom")
        worker = _make_worker(pool)

        with patch(
            "factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()
        ) as mock_pje:
            await worker.handle(msg=MagicMock(), payload=_VALID_PAYLOAD)
            await _drain(worker)
            mock_pje.assert_awaited_once()
            # publish_job_error(nc, job_id, exc) → exc is the 3rd positional arg.
            exc_arg = mock_pje.call_args.args[2]
            assert isinstance(exc_arg, RuntimeError)

    async def test_error_publishes_correct_job_id(self) -> None:
        pool = _make_pool()
        fake_pw = pool.acquire.return_value
        fake_pw.bridge.run.side_effect = ValueError("oops")
        worker = _make_worker(pool)

        with patch(
            "factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()
        ) as mock_pje:
            await worker.handle(msg=MagicMock(), payload=_VALID_PAYLOAD)
            await _drain(worker)
            # publish_job_error(nc, job_id, exc) → job_id is the 2nd positional arg.
            job_id_arg = mock_pje.call_args.args[1]
            assert job_id_arg == _JOB_ID

    async def test_pool_released_even_when_bridge_run_raises(self) -> None:
        pool = _make_pool()
        fake_pw = pool.acquire.return_value
        fake_pw.bridge.run.side_effect = RuntimeError("crash")
        worker = _make_worker(pool)

        with patch("factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()):
            await worker.handle(msg=MagicMock(), payload=_VALID_PAYLOAD)
            await _drain(worker)

        pool.release.assert_called_once_with(fake_pw)


# ---------------------------------------------------------------------------
# run() — pool lifecycle (register before loop, aclose in finally)
# ---------------------------------------------------------------------------


class TestRun:
    async def test_run_registers_pool_before_loop(self) -> None:
        """pool.register(nc) must be awaited before run_embedded is called."""
        pool = _make_pool()
        worker = _make_worker(pool)

        call_order: list[str] = []

        async def mock_nats_connect(*a, **kw):  # noqa: ARG001
            call_order.append("connect")
            return AsyncMock()

        async def mock_register(nc):  # noqa: ARG001
            call_order.append("register")

        async def mock_run_embedded(nc, stop):
            call_order.append("run_embedded")
            stop.set()

        pool.register.side_effect = mock_register

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

        assert call_order.index("register") < call_order.index("run_embedded")
        pool.register.assert_awaited_once()

    async def test_run_closes_pool_after_loop(self) -> None:
        """pool.aclose() must run in finally: register < run_embedded < aclose."""
        pool = _make_pool()
        worker = _make_worker(pool)

        call_order: list[str] = []

        async def mock_nats_connect(*a, **kw):  # noqa: ARG001
            return AsyncMock()

        async def mock_register(nc):  # noqa: ARG001
            call_order.append("register")

        async def mock_run_embedded(nc, stop):  # noqa: ARG001
            call_order.append("run_embedded")

        async def mock_aclose():
            call_order.append("aclose")

        pool.register.side_effect = mock_register
        pool.aclose.side_effect = mock_aclose

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
        pool.aclose.assert_awaited_once()

    async def test_run_closes_pool_when_loop_raises(self) -> None:
        """pool.aclose() runs via finally even when the loop raises."""
        pool = _make_pool()
        worker = _make_worker(pool)

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

        pool.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# handle() — backward compatibility: old-style payload (prompt only)
# ---------------------------------------------------------------------------


class TestHandleBackwardCompat:
    async def test_handle_prompt_only_payload_backward_compat(self) -> None:
        """Old-style envelope with only {prompt} in payload must call bridge.run."""
        pool = _make_pool()
        fake_pw = pool.acquire.return_value
        worker = _make_worker(pool)
        payload = {
            "job_id": _JOB_ID,
            "job_name": _JOB_NAME,
            "payload": {
                "prompt": "x",
                "pool_id": _POOL_ID,
            },
            "contract_version": "1",
            "reply_to": "_INBOX.test.reply",
            "trace_id": "trace-bc-001",
            "issued_at": "2026-06-11T00:00:00Z",
        }
        with patch("factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()):
            await worker.handle(msg=MagicMock(), payload=payload)
            await _drain(worker)

        fake_pw.bridge.run.assert_awaited_once_with(
            "x",
            _JOB_ID,
            session_file=ANY,
        )

    async def test_handle_reads_model_cfg_and_system_prompt(self) -> None:
        """Enriched payload (model_cfg + system_prompt) must not raise."""
        pool = _make_pool()
        fake_pw = pool.acquire.return_value
        worker = _make_worker(pool)
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
        with patch("factory.adapters.omp.omp_worker.publish_job_error", AsyncMock()):
            await worker.handle(msg=MagicMock(), payload=payload)
            await _drain(worker)

        fake_pw.bridge.run.assert_awaited_once_with(
            "x",
            _JOB_ID,
            session_file=ANY,
        )
