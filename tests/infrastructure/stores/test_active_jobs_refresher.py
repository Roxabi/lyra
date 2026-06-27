"""Unit tests for RegistryCoordinator (#1796, slice 2).

All tests use an AsyncMock port.  No asyncio.sleep / time.sleep calls —
the background loop (_loop) is never exercised here; refresh_all, on_heartbeat,
open, and close are tested directly.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock

import nats.errors
import pytest

from factory.core.ports.active_jobs import ActiveJobEntry, RegistryConflictError
from factory.infrastructure.stores.jobs.active_jobs_refresher import RegistryCoordinator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entry(
    job_id: str,
    pool_id: str = "pool-a",
    worker_loc: str | None = None,
    concurrency_mode: str = "steer",
) -> ActiveJobEntry:
    return ActiveJobEntry(
        job_id=job_id,
        pool_id=pool_id,
        status="open",
        started_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        steer_subject=f"factory.steer.{job_id}",
        concurrency_mode=concurrency_mode,
        worker_loc=worker_loc,
    )


@pytest.fixture()
def port() -> AsyncMock:
    mock = AsyncMock()
    mock.open = AsyncMock(return_value=None)
    mock.close = AsyncMock(return_value=None)
    mock.refresh = AsyncMock(return_value=None)
    return mock


@pytest.fixture()
def coord(port: AsyncMock) -> RegistryCoordinator:
    return RegistryCoordinator(port)


# ---------------------------------------------------------------------------
# open()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_open_records_entry_in_jobs_map(coord: RegistryCoordinator) -> None:
    entry = _make_entry("job-1", worker_loc=None)
    await coord.open(entry)
    assert "job-1" in coord._jobs


@pytest.mark.asyncio()
async def test_open_records_worker_loc_in_by_loc(coord: RegistryCoordinator) -> None:
    entry = _make_entry("job-2", worker_loc="host-a:9000")
    await coord.open(entry)
    assert coord._by_loc.get("host-a:9000") == "job-2"


@pytest.mark.asyncio()
async def test_open_no_worker_loc_not_added_to_by_loc(
    coord: RegistryCoordinator,
) -> None:
    entry = _make_entry("job-3", worker_loc=None)
    await coord.open(entry)
    assert "job-3" in coord._jobs
    assert not coord._by_loc


@pytest.mark.asyncio()
async def test_open_conflict_not_recorded_and_raises(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    port.open.side_effect = RegistryConflictError("pool-a", "existing-job")
    entry = _make_entry("job-conflict")
    with pytest.raises(RegistryConflictError):
        await coord.open(entry)
    assert "job-conflict" not in coord._jobs
    assert not coord._by_loc


# ---------------------------------------------------------------------------
# refresh_all()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_refresh_all_calls_refresh_for_each_open_job(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    await coord.open(_make_entry("job-a"))
    await coord.open(_make_entry("job-b", pool_id="pool-b"))
    port.refresh.reset_mock()

    await coord.refresh_all()

    assert port.refresh.call_count == 2
    port.refresh.assert_any_call("job-a")
    port.refresh.assert_any_call("job-b")


@pytest.mark.asyncio()
async def test_refresh_all_empty_no_calls(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    await coord.refresh_all()
    port.refresh.assert_not_called()


@pytest.mark.asyncio()
async def test_refresh_all_one_failure_does_not_abort_sweep(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    """One job's refresh error must not abort the sweep for the rest.

    Regression guard: if a single transient NATS error propagated, the liveness
    loop would die and ALL tracked jobs would silently go stale → pools wrongly
    freed while their jobs still run (defeats the singleton guarantee).
    """
    await coord.open(_make_entry("job-x"))
    await coord.open(_make_entry("job-y", pool_id="pool-b"))
    port.refresh.reset_mock()
    # First job in the (insertion-ordered) snapshot raises; second must still run.
    port.refresh.side_effect = [nats.errors.Error("transient NATS error"), None]

    await coord.refresh_all()  # must not raise

    assert port.refresh.call_count == 2
    port.refresh.assert_any_call("job-x")
    port.refresh.assert_any_call("job-y")


@pytest.mark.asyncio()
async def test_refresh_all_runtime_error_does_not_abort_sweep(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    """Non-NATS refresh failures must not abort the liveness sweep."""
    await coord.open(_make_entry("job-a"))
    await coord.open(_make_entry("job-b", pool_id="pool-b"))
    port.refresh.reset_mock()
    port.refresh.side_effect = [RuntimeError("store bug"), None]

    await coord.refresh_all()

    assert port.refresh.call_count == 2


# ---------------------------------------------------------------------------
# on_heartbeat()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_on_heartbeat_refreshes_matching_worker(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    await coord.open(_make_entry("job-hb", worker_loc="loc-A"))
    port.refresh.reset_mock()

    await coord.on_heartbeat("loc-A")

    port.refresh.assert_called_once_with("job-hb")


@pytest.mark.asyncio()
async def test_on_heartbeat_unknown_loc_does_not_call_refresh(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    await coord.open(_make_entry("job-hb2", worker_loc="loc-B"))
    port.refresh.reset_mock()

    await coord.on_heartbeat("loc-UNKNOWN")

    port.refresh.assert_not_called()


@pytest.mark.asyncio()
async def test_on_heartbeat_only_matching_loc_refreshed(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    """Two jobs with different worker_loc.

    Heartbeat for one must not touch the other.
    """
    await coord.open(_make_entry("job-x", pool_id="pool-x", worker_loc="loc-X"))
    await coord.open(_make_entry("job-y", pool_id="pool-y", worker_loc="loc-Y"))
    port.refresh.reset_mock()

    await coord.on_heartbeat("loc-X")

    port.refresh.assert_called_once_with("job-x")


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_close_calls_port_close(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    await coord.open(_make_entry("job-c"))
    port.close.reset_mock()

    await coord.close("job-c")

    port.close.assert_called_once_with("job-c")


@pytest.mark.asyncio()
async def test_close_removes_from_jobs_map(coord: RegistryCoordinator) -> None:
    await coord.open(_make_entry("job-d"))
    await coord.close("job-d")
    assert "job-d" not in coord._jobs


@pytest.mark.asyncio()
async def test_close_removes_worker_loc_from_by_loc(coord: RegistryCoordinator) -> None:
    await coord.open(_make_entry("job-e", worker_loc="loc-E"))
    await coord.close("job-e")
    assert "loc-E" not in coord._by_loc


@pytest.mark.asyncio()
async def test_closed_job_not_refreshed_by_refresh_all(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    await coord.open(_make_entry("job-f"))
    await coord.close("job-f")
    port.refresh.reset_mock()

    await coord.refresh_all()

    port.refresh.assert_not_called()


@pytest.mark.asyncio()
async def test_closed_job_heartbeat_no_longer_refreshes(
    port: AsyncMock, coord: RegistryCoordinator
) -> None:
    await coord.open(_make_entry("job-g", worker_loc="loc-G"))
    await coord.close("job-g")
    port.refresh.reset_mock()

    await coord.on_heartbeat("loc-G")

    port.refresh.assert_not_called()
