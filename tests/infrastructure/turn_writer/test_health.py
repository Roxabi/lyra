"""Tests for TurnWriterHealthServer /health and /metrics endpoints (issue #1331 — T31).

Strategy: build the FastAPI app via TurnWriterHealthServer._build_app(), drive it
with httpx.ASGITransport — no real socket listening.

Mock writer / store / nc objects via MagicMock — only the attributes the endpoints
touch (_task, oldest_pending, _db, is_connected).

Cases:
  1. /health 200 when writer task alive + nc connected + store open
  2. /health 503 when writer._task is None (not started)
  3. /health 503 when nc.is_connected is False
  4. /health 503 when store._db is None (not open)
  5. /health 503 includes all failing reasons
  6. /metrics 200 + turn_writer_lag_seconds 0.0 when oldest_pending is None
  7. /metrics elapsed seconds when oldest_pending is set in the past
  8. /metrics value increases over time when oldest_pending is fixed
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, PropertyMock

import pytest
from httpx import ASGITransport, AsyncClient

from lyra.infrastructure.turn_writer.health import TurnWriterHealthServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server(
    *,
    task_alive: bool = True,
    nc_connected: bool = True,
    store_open: bool = True,
    oldest_pending: datetime | None = None,
) -> TurnWriterHealthServer:
    """Build a TurnWriterHealthServer with fully mocked dependencies."""
    task_mock = MagicMock()
    task_mock.done.return_value = False  # task is alive when done() == False

    writer = MagicMock()
    writer._task = task_mock if task_alive else None
    writer.oldest_pending = oldest_pending

    store = MagicMock()
    store._db = MagicMock() if store_open else None

    nc = MagicMock()
    type(nc).is_connected = PropertyMock(return_value=nc_connected)

    return TurnWriterHealthServer(writer, store, nc)


async def _get(app, path: str) -> tuple[int, dict | str]:
    """Issue a GET request via ASGITransport; return (status_code, body)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path)
    content_type = resp.headers.get("content-type", "")
    if "json" in content_type:
        return resp.status_code, resp.json()
    return resp.status_code, resp.text


# ---------------------------------------------------------------------------
# /health tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_200_when_all_healthy(self) -> None:
        """Case 1: all three conditions met → 200 OK, ok=True, no reasons."""
        server = _make_server()
        status, body = await _get(server.app, "/health")
        assert isinstance(body, dict)
        assert status == 200
        assert body["ok"] is True
        assert "reasons" not in body

    async def test_503_when_writer_task_none(self) -> None:
        """Case 2: writer._task is None → 503, ok=False, writer_task_not_alive."""
        server = _make_server(task_alive=False)
        status, body = await _get(server.app, "/health")
        assert isinstance(body, dict)
        assert status == 503
        assert body["ok"] is False
        assert "writer_task_not_alive" in body["reasons"]

    async def test_503_when_nats_disconnected(self) -> None:
        """Case 3: nc.is_connected is False → 503, nats_disconnected in reasons."""
        server = _make_server(nc_connected=False)
        status, body = await _get(server.app, "/health")
        assert isinstance(body, dict)
        assert status == 503
        assert body["ok"] is False
        assert "nats_disconnected" in body["reasons"]

    async def test_503_when_store_not_open(self) -> None:
        """Case 4: store._db is None → 503, store_not_open in reasons."""
        server = _make_server(store_open=False)
        status, body = await _get(server.app, "/health")
        assert isinstance(body, dict)
        assert status == 503
        assert body["ok"] is False
        assert "store_not_open" in body["reasons"]

    async def test_503_includes_all_failing_reasons(self) -> None:
        """Case 5: all three conditions fail → three reasons present."""
        server = _make_server(task_alive=False, nc_connected=False, store_open=False)
        status, body = await _get(server.app, "/health")
        assert isinstance(body, dict)
        assert status == 503
        assert body["ok"] is False
        reasons = body["reasons"]
        assert "writer_task_not_alive" in reasons
        assert "nats_disconnected" in reasons
        assert "store_not_open" in reasons

    async def test_503_when_task_done(self) -> None:
        """Edge case: task exists but is done (crashed) → 503."""
        done_task: MagicMock = MagicMock()
        done_task.done.return_value = True  # task finished / crashed

        writer: MagicMock = MagicMock()
        writer._task = done_task
        writer.oldest_pending = None

        store: MagicMock = MagicMock()
        store._db = MagicMock()

        nc: MagicMock = MagicMock()
        type(nc).is_connected = PropertyMock(return_value=True)

        server = TurnWriterHealthServer(writer, store, nc)
        status, body = await _get(server.app, "/health")
        assert isinstance(body, dict)
        assert status == 503
        assert "writer_task_not_alive" in body["reasons"]


# ---------------------------------------------------------------------------
# /metrics tests
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    async def test_200_with_zero_lag_when_no_pending(self) -> None:
        """Case 6: oldest_pending=None → turn_writer_lag_seconds 0.0 (or 0)."""
        server = _make_server(oldest_pending=None)
        status, body = await _get(server.app, "/metrics")
        assert status == 200
        assert isinstance(body, str)
        assert "# HELP turn_writer_lag_seconds" in body
        assert "# TYPE turn_writer_lag_seconds gauge" in body
        # Value is 0.0
        line = next(
            ln for ln in body.splitlines() if ln.startswith("turn_writer_lag_seconds ")
        )
        value = float(line.split()[-1])
        assert value == pytest.approx(0.0)

    async def test_elapsed_seconds_when_oldest_pending_set(self) -> None:
        """Case 7: oldest_pending set 5s ago → lag ≈ 5s."""
        five_sec_ago = datetime.now(UTC) - timedelta(seconds=5)
        server = _make_server(oldest_pending=five_sec_ago)
        status, body = await _get(server.app, "/metrics")
        assert status == 200
        assert isinstance(body, str)
        line = next(
            ln for ln in body.splitlines() if ln.startswith("turn_writer_lag_seconds ")
        )
        value = float(line.split()[-1])
        # Allow generous tolerance for test execution time
        assert 4.5 <= value <= 7.0

    async def test_lag_increases_when_oldest_pending_fixed(self) -> None:
        """Case 8: two calls with same fixed oldest_pending → value grows."""
        fixed_time = datetime.now(UTC) - timedelta(seconds=2)
        server = _make_server(oldest_pending=fixed_time)

        _, body1 = await _get(server.app, "/metrics")
        assert isinstance(body1, str)
        line1 = next(
            ln for ln in body1.splitlines() if ln.startswith("turn_writer_lag_seconds ")
        )
        lag1 = float(line1.split()[-1])

        # Brief asyncio yield so clock advances measurably
        import asyncio

        await asyncio.sleep(0.05)

        _, body2 = await _get(server.app, "/metrics")
        assert isinstance(body2, str)
        line2 = next(
            ln for ln in body2.splitlines() if ln.startswith("turn_writer_lag_seconds ")
        )
        lag2 = float(line2.split()[-1])

        assert lag2 > lag1, f"Expected lag2 ({lag2}) > lag1 ({lag1})"
