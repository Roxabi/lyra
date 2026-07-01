"""Jobs SSE generator unit tests (#1772)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from factory.dashboard.jobs_stream import jobs_sse_events
from roxabi_contracts.dashboard import DashboardJobsListResponse


@pytest.mark.asyncio
async def test_jobs_sse_emits_snapshot_then_stops() -> None:
    hub = AsyncMock()
    hub.list_jobs = AsyncMock(return_value=DashboardJobsListResponse(jobs=[]))
    calls = 0

    async def connected() -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    frames: list[str] = []
    async for frame in jobs_sse_events(hub, is_connected=connected):
        frames.append(frame)

    assert len(frames) == 1
    payload = json.loads(frames[0].removeprefix("data: ").strip())
    assert payload["type"] == "snapshot"
    assert payload["jobs"] == []


@pytest.mark.asyncio
async def test_jobs_sse_ping_when_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")

    async def noop_sleep(
        _seconds: float,
        *,
        is_connected: object = None,
    ) -> None:
        return

    monkeypatch.setattr(
        "factory.dashboard.jobs_stream._interruptible_sleep",
        noop_sleep,
    )
    loops = 0

    async def connected() -> bool:
        nonlocal loops
        loops += 1
        return loops <= 2

    hub = AsyncMock()
    frames: list[str] = []
    async for frame in jobs_sse_events(hub, is_connected=connected):
        frames.append(frame)

    assert len(frames) == 2
    first = json.loads(frames[0].removeprefix("data: ").strip())
    second = json.loads(frames[1].removeprefix("data: ").strip())
    assert first["type"] == "snapshot"
    assert second["type"] == "ping"
    hub.list_jobs.assert_not_awaited()


@pytest.mark.asyncio
async def test_jobs_sse_emits_generic_error_frame() -> None:
    hub = AsyncMock()
    hub.list_jobs = AsyncMock(side_effect=RuntimeError("internal kv detail"))
    calls = 0

    async def connected() -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    frames: list[str] = []
    async for frame in jobs_sse_events(hub, is_connected=connected):
        frames.append(frame)

    assert len(frames) == 1
    payload = json.loads(frames[0].removeprefix("data: ").strip())
    assert payload["type"] == "error"
    # Generic message only — internal exception text must NOT leak (review #2125).
    assert payload["message"] == "snapshot unavailable"
    assert "internal kv detail" not in frames[0]
