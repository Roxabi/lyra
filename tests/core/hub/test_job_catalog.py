"""Tests for job_catalog.list_active_jobs (#1772)."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.core.hub.hub_protocol import Binding, RoutingKey
from factory.core.hub.job_catalog import list_active_jobs
from factory.core.messaging.message import Platform
from factory.core.ports.active_jobs import ActiveJobEntry
from factory.infrastructure.stores.jobs.active_jobs_refresher import RegistryCoordinator

_TS = datetime.datetime(2026, 6, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.mark.asyncio
async def test_list_active_jobs_from_coordinator_snapshot() -> None:
    hub = MagicMock()
    hub.bindings = {
        RoutingKey(Platform.WEB, "smoke", "agent:lyra"): Binding(
            agent_name="lyra", pool_id="web:smoke:agent:lyra"
        ),
    }
    port = AsyncMock()
    coord = RegistryCoordinator(port)
    await coord.open(
        ActiveJobEntry(
            job_id="j1",
            pool_id="web:smoke:agent:lyra",
            status="open",
            started_at=_TS,
            steer_subject="factory.job.j1.steer",
            concurrency_mode="steer",
        )
    )
    hub._active_jobs_coord = coord

    rows = await list_active_jobs(hub)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "j1"
    assert rows[0]["agent"] == "lyra"
    assert rows[0]["platform"] == "web"