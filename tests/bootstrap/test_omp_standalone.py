"""Bootstrap wiring tests for _bootstrap_omp_standalone (#1813)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
    """OmpPool() built with no args; injected into OmpWorker(pool=...)."""
    from factory.bootstrap.standalone.worker_standalone import (
        _bootstrap_omp_standalone,
    )

    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

    mock_pool = MagicMock()
    mock_worker = MagicMock()
    mock_worker.run = AsyncMock()

    mock_pool_cls = MagicMock(return_value=mock_pool)
    mock_worker_cls = MagicMock(return_value=mock_worker)

    with (
        patch("factory.bootstrap.standalone.worker_standalone._export_secret_file"),
        patch("factory.bootstrap.standalone.worker_standalone.run_git_ownership_probe"),
        patch("factory.bootstrap.standalone.worker_standalone.log_contracts_version"),
        patch("factory.adapters.omp.omp_pool.OmpPool", mock_pool_cls),
        patch("factory.adapters.omp.omp_worker.OmpWorker", mock_worker_cls),
    ):
        await _bootstrap_omp_standalone({})

    # Pool constructed with no positional or keyword args.
    mock_pool_cls.assert_called_once_with()
    # Worker injected with the constructed pool.
    mock_worker_cls.assert_called_once_with(pool=mock_pool, identity_name="omp-worker")
    # Worker.run called with the NATS URL.
    mock_worker.run.assert_awaited_once_with("nats://localhost:4222")
