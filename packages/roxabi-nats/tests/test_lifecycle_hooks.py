"""Lifecycle hook integration tests for NatsAdapterBase (#2069)."""

from __future__ import annotations

import asyncio

import pytest

from roxabi_nats.adapter_base import NatsAdapterBase


class _RecordingHooks:
    def __init__(self) -> None:
        self.starts: list[str] = []
        self.ends: list[tuple[str, float]] = []
        self.attrs: list[tuple[str, dict]] = []

    def on_work_start(self, *, job_id: str, **kwargs: object) -> None:
        self.starts.append(job_id)

    def on_work_end(
        self, *, job_id: str, duration_ms: float, **kwargs: object
    ) -> None:
        self.ends.append((job_id, duration_ms))

    def record_domain_attrs(self, job_id: str, attrs: dict) -> None:
        self.attrs.append((job_id, dict(attrs)))


class _SyncWorker(NatsAdapterBase):
    def __init__(self, hooks: _RecordingHooks) -> None:
        super().__init__(
            subject="factory.jobs.test",
            queue_group="test-workers",
            envelope_name="JobEnvelope",
            schema_version=1,
            lifecycle_hooks=hooks,
            wait_ready=False,
        )

    async def handle(self, msg: object, payload: dict) -> None:
        await asyncio.sleep(0.05)


class _BackgroundWorker(NatsAdapterBase):
    def __init__(self, hooks: _RecordingHooks) -> None:
        super().__init__(
            subject="factory.jobs.test",
            queue_group="test-workers",
            envelope_name="JobEnvelope",
            schema_version=1,
            lifecycle_hooks=hooks,
            wait_ready=False,
        )
        self._jobs: set[asyncio.Task] = set()

    def _defer_hooks_to_background(self) -> bool:
        return True

    async def handle(self, msg: object, payload: dict) -> None:
        task = asyncio.create_task(
            self._run_with_work_hooks(payload, self._do_work)
        )
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def _do_work(self) -> None:
        await asyncio.sleep(0.05)


class TestLifecycleHooks:
    @pytest.mark.asyncio
    async def test_sync_worker_hooks_cover_handle_duration(self) -> None:
        hooks = _RecordingHooks()
        worker = _SyncWorker(hooks)
        payload = {"trace_id": "trace-1", "job_id": "a" * 32}
        await worker._invoke_handle_with_hooks(object(), payload)
        assert hooks.starts == ["a" * 32]
        assert len(hooks.ends) == 1
        assert hooks.ends[0][1] >= 40.0

    @pytest.mark.asyncio
    async def test_background_worker_hooks_wrap_job_not_dispatch(self) -> None:
        hooks = _RecordingHooks()
        worker = _BackgroundWorker(hooks)
        payload = {"trace_id": "trace-2", "job_id": "b" * 32}
        await worker.handle(object(), payload)
        await asyncio.sleep(0.15)
        assert hooks.starts == ["b" * 32]
        assert len(hooks.ends) == 1
        assert hooks.ends[0][1] >= 40.0