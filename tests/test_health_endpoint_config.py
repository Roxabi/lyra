"""Tests for /config endpoint and /health reaper fields.

Covers: issue #135, #207, #317, SC-11.
Classes: TestConfigEndpoint, TestHealthReaperFields.
"""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from lyra.bootstrap.infra.health import Secrets, create_health_app
from lyra.core.hub import Hub
from tests.conftest import AUTH_HEADERS, HEALTH_SECRET

# ---------------------------------------------------------------------------
# T3 — GET /config endpoint (issue #135)
# ---------------------------------------------------------------------------


class TestConfigEndpoint:
    """GET /config endpoint auth and 404 behavior."""

    async def test_config_returns_404_when_no_anthropic_agent(self) -> None:
        """No agent registered → 404."""
        # Arrange
        from unittest.mock import Mock

        mock_secrets = Mock(spec=Secrets, health_secret="test-config-secret")
        test_hub = Hub()
        app = create_health_app(test_hub, secrets=mock_secrets)
        transport = ASGITransport(app=app)

        # Act
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/config", headers={"authorization": "Bearer test-config-secret"}
            )

        # Assert
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# T4 — /health reaper fields (#317)
# ---------------------------------------------------------------------------


class TestHealthReaperFields:
    """#317 SC-11: /health/detail includes reaper_alive and reaper_last_sweep_age."""

    @pytest.fixture(autouse=True)
    def mock_secrets(self) -> Secrets:
        from unittest.mock import Mock

        return Mock(spec=Secrets, health_secret=HEALTH_SECRET)

    async def test_reaper_fields_absent_when_no_cli_pool(
        self, hub: Hub, mock_secrets: Secrets
    ) -> None:
        """No cli_pool → reaper keys omitted entirely from response."""
        assert hub.cli_pool is None
        app = create_health_app(hub, secrets=mock_secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert "reaper_alive" not in data
        assert "reaper_last_sweep_age" not in data

    async def test_reaper_fields_with_live_cli_pool(
        self, hub: Hub, mock_secrets: Secrets
    ) -> None:
        """cli_pool with active reaper → reaper_alive=True."""
        from unittest.mock import MagicMock

        from lyra.core.cli.cli_pool import CliPool

        cli_pool = CliPool()
        # Simulate a running reaper task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        cli_pool._reaper_task = mock_task
        cli_pool._last_sweep_at = time.monotonic() - 30  # 30s ago
        hub.cli_pool = cli_pool

        app = create_health_app(hub, secrets=mock_secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["reaper_alive"] is True
        assert data["reaper_last_sweep_age"] is not None
        assert 25 <= data["reaper_last_sweep_age"] <= 35

    async def test_reaper_fields_before_first_sweep(
        self, hub: Hub, mock_secrets: Secrets
    ) -> None:
        """cli_pool started but no sweep yet → reaper_alive=True, age=None."""
        from unittest.mock import MagicMock

        from lyra.core.cli.cli_pool import CliPool

        cli_pool = CliPool()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        cli_pool._reaper_task = mock_task
        cli_pool._last_sweep_at = None
        hub.cli_pool = cli_pool

        app = create_health_app(hub, secrets=mock_secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["reaper_alive"] is True
        assert data["reaper_last_sweep_age"] is None
