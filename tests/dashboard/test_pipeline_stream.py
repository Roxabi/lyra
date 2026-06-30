"""Pipeline SSE generator unit tests."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from factory.dashboard.pipeline_stream import pipeline_sse_events
from roxabi_contracts.dashboard import DashboardPipelineResponse


@pytest.mark.asyncio
async def test_pipeline_sse_emits_snapshot_then_stops() -> None:
    hub = AsyncMock()
    hub.pipeline_list = AsyncMock(return_value=DashboardPipelineResponse(runs=[]))
    calls = 0

    async def connected() -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    frames: list[str] = []
    async for frame in pipeline_sse_events(hub, is_connected=connected):
        frames.append(frame)

    assert len(frames) == 1
    payload = json.loads(frames[0].removeprefix("data: ").strip())
    assert payload["type"] == "snapshot"
    assert payload["runs"] == []


@pytest.mark.asyncio
async def test_pipeline_sse_ping_when_unchanged(
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
        "factory.dashboard.pipeline_stream._interruptible_sleep",
        noop_sleep,
    )
    loops = 0

    async def connected() -> bool:
        nonlocal loops
        loops += 1
        return loops <= 2

    hub = AsyncMock()
    frames: list[str] = []
    async for frame in pipeline_sse_events(hub, is_connected=connected):
        frames.append(frame)

    assert len(frames) == 2
    first = json.loads(frames[0].removeprefix("data: ").strip())
    second = json.loads(frames[1].removeprefix("data: ").strip())
    assert first["type"] == "snapshot"
    assert second["type"] == "ping"
    hub.pipeline_list.assert_not_awaited()