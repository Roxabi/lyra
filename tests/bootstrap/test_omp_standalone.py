"""Bootstrap wiring tests for _bootstrap_omp_standalone (#1813)."""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest


async def test_missing_nats_url_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """NATS_URL unset → sys.exit before any OmpPool/OmpWorker is constructed."""
    from factory.bootstrap.standalone.worker_standalone import (
        _bootstrap_omp_standalone,
    )

    monkeypatch.delenv("NATS_URL", raising=False)

    with (
        patch("factory.bootstrap.standalone.worker_standalone._export_secret_file"),
        patch("factory.bootstrap.standalone.worker_standalone.run_git_ownership_probe"),
        pytest.raises(SystemExit),
    ):
        await _bootstrap_omp_standalone({})


async def test_happy_path_wires_pool_into_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OmpPool() with no args; OmpWorker(pool=...); embedded NATS via nats_connect."""
    from factory.bootstrap.standalone.worker_standalone import (
        _bootstrap_omp_standalone,
    )

    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

    stop = asyncio.Event()
    stop.set()

    mock_nc = AsyncMock()
    mock_nc.close = AsyncMock()

    mock_pool = MagicMock()
    mock_worker = MagicMock()
    mock_worker.run_embedded = AsyncMock()

    mock_pool_cls = MagicMock(return_value=mock_pool)
    mock_worker_cls = MagicMock(return_value=mock_worker)

    with (
        patch("factory.bootstrap.standalone.worker_standalone._export_secret_file"),
        patch("factory.bootstrap.standalone.worker_standalone.run_git_ownership_probe"),
        patch("factory.bootstrap.standalone.worker_standalone.log_contracts_version"),
        patch(
            "factory.bootstrap.standalone.worker_standalone.nats_connect",
            AsyncMock(return_value=mock_nc),
        ),
        patch(
            "factory.bootstrap.fleet_reporter.start_fleet_reporter",
            AsyncMock(return_value=None),
        ),
        patch(
            "factory.bootstrap.fleet_reporter.cancel_fleet_reporter",
            AsyncMock(),
        ),
        patch(
            "factory.bootstrap.standalone.worker_standalone.setup_shutdown_event",
            return_value=stop,
        ),
        patch("factory.adapters.omp.omp_pool.OmpPool", mock_pool_cls),
        patch("factory.adapters.omp.omp_worker.OmpWorker", mock_worker_cls),
    ):
        await _bootstrap_omp_standalone({})

    mock_pool_cls.assert_called_once_with()
    mock_worker_cls.assert_called_once_with(
        pool=mock_pool,
        identity_name="omp-worker",
        lifecycle_hooks=ANY,
    )
    mock_worker.run_embedded.assert_awaited_once_with(mock_nc, stop)
    mock_nc.close.assert_awaited_once()