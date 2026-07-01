"""Pool run lifecycle → active-jobs registry write-back (#1772).

The registry write is a best-effort side-channel: it must register a run's
open/close, but ANY failure (registry down, missing recorder, test double)
must be swallowed so message processing is never broken.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.core.pool.pool_processor import _close_active_job, _open_active_job


class _Recorder:
    def __init__(self) -> None:
        self.opened: list[Any] = []
        self.closed: list[str] = []

    async def open(self, entry: Any) -> None:
        self.opened.append(entry)

    async def close(self, job_id: str) -> None:
        self.closed.append(job_id)


def _pool_with(recorder: object | None) -> Any:
    ctx = SimpleNamespace(active_jobs_recorder=lambda: recorder)
    return SimpleNamespace(pool_id="web:smoke:agent:lyra", _ctx=ctx)


@pytest.mark.asyncio
async def test_open_registers_entry_and_returns_job_id() -> None:
    rec = _Recorder()
    pool = _pool_with(rec)

    job_id = await _open_active_job(pool)

    assert job_id is not None
    assert len(rec.opened) == 1
    entry = rec.opened[0]
    assert entry.job_id == job_id
    assert entry.pool_id == "web:smoke:agent:lyra"
    assert entry.status == "open"
    assert entry.concurrency_mode == "steer"
    assert entry.steer_subject == f"factory.job.{job_id}.steer"


@pytest.mark.asyncio
async def test_close_removes_entry() -> None:
    rec = _Recorder()
    pool = _pool_with(rec)

    await _close_active_job(pool, "job-123")

    assert rec.closed == ["job-123"]


@pytest.mark.asyncio
async def test_no_recorder_is_noop() -> None:
    pool = _pool_with(None)

    assert await _open_active_job(pool) is None
    await _close_active_job(pool, "job-x")  # must not raise


@pytest.mark.asyncio
async def test_open_swallows_recorder_error() -> None:
    rec = MagicMock()
    rec.open = AsyncMock(side_effect=RuntimeError("kv down"))
    pool = _pool_with(rec)

    # Registry down must NOT break the turn — returns None, never raises.
    assert await _open_active_job(pool) is None


@pytest.mark.asyncio
async def test_close_swallows_recorder_error() -> None:
    rec = MagicMock()
    rec.close = AsyncMock(side_effect=RuntimeError("kv down"))
    pool = _pool_with(rec)

    await _close_active_job(pool, "job-1")  # must not raise


@pytest.mark.asyncio
async def test_magicmock_ctx_does_not_break() -> None:
    # A MagicMock ctx exposes a callable active_jobs_recorder whose returned
    # mock is not awaitable — the error must be swallowed, never propagate.
    pool: Any = SimpleNamespace(pool_id="p", _ctx=MagicMock())

    assert await _open_active_job(pool) is None
