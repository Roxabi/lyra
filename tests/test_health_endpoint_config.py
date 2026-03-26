"""Tests for /config endpoint and /health reaper fields.

Covers: issue #135, #207, #317, SC-11.
Classes: TestConfigEndpoint, TestHealthReaperFields.
"""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from lyra.core.hub import Hub
from tests.conftest import AUTH_HEADERS, HEALTH_SECRET

# ---------------------------------------------------------------------------
# T3 — GET /config endpoint (issue #135)
# ---------------------------------------------------------------------------


class TestConfigEndpoint:
    """GET /config exposes runtime config when an AnthropicAgent is registered."""

    async def test_config_returns_correct_json_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AnthropicAgent registered as lyra_default → 200 with all expected keys."""
        # Arrange
        monkeypatch.setenv("LYRA_CONFIG_SECRET", "test-config-secret")
        from unittest.mock import AsyncMock, MagicMock

        from lyra.agents.anthropic_agent import AnthropicAgent
        from lyra.bootstrap.health import create_health_app
        from lyra.core.agent import Agent
        from lyra.core.agent_config import ModelConfig
        from lyra.core.runtime_config import RuntimeConfig
        from lyra.llm.base import LlmResult

        config = Agent(
            name="lyra_default",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
            llm_config=ModelConfig(
                backend="anthropic-sdk",
                model="claude-sonnet-4-5",
                max_turns=10,
                tools=(),
            ),
        )
        runtime_config = RuntimeConfig(
            style="concise",
            language="auto",
            temperature=0.7,
        )
        mock_provider = MagicMock()
        mock_provider.capabilities = {"streaming": False, "auth": "api_key"}
        mock_provider.complete = AsyncMock(return_value=LlmResult(result="ok"))
        test_hub = Hub()
        agent = AnthropicAgent(config, mock_provider, runtime_config=runtime_config)
        test_hub.register_agent(agent)

        app = create_health_app(test_hub)
        transport = ASGITransport(app=app)

        # Act
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/config", headers={"authorization": "Bearer test-config-secret"}
            )

        # Assert
        assert resp.status_code == 200
        data = resp.json()
        assert "style" in data
        assert "language" in data
        assert "temperature" in data
        assert "model" in data
        assert "max_steps" in data
        assert "extra_instructions" in data
        assert "effective_model" in data
        assert "effective_max_steps" in data
        # Spot-check values
        assert data["style"] == "concise"
        assert data["language"] == "auto"
        assert data["temperature"] == 0.7
        assert data["effective_model"] == "claude-sonnet-4-5"
        assert data["effective_max_steps"] == 10

    async def test_config_returns_404_when_no_anthropic_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No agent (or non-AnthropicAgent) registered → 404."""
        # Arrange
        monkeypatch.setenv("LYRA_CONFIG_SECRET", "test-config-secret")
        from lyra.bootstrap.health import create_health_app

        test_hub = Hub()
        app = create_health_app(test_hub)
        transport = ASGITransport(app=app)

        # Act
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/config", headers={"authorization": "Bearer test-config-secret"}
            )

        # Assert
        assert resp.status_code == 404

    async def test_config_returns_401_without_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#207: /config without auth returns 401."""
        monkeypatch.setenv("LYRA_CONFIG_SECRET", "test-config-secret")
        from lyra.bootstrap.health import create_health_app

        app = create_health_app(Hub())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/config")

        assert resp.status_code == 401

    async def test_config_returns_401_with_wrong_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#207: /config with wrong token returns 401."""
        monkeypatch.setenv("LYRA_CONFIG_SECRET", "test-config-secret")
        from lyra.bootstrap.health import create_health_app

        app = create_health_app(Hub())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/config", headers={"authorization": "Bearer wrong"}
            )

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# T4 — /health reaper fields (#317)
# ---------------------------------------------------------------------------


class TestHealthReaperFields:
    """#317 SC-11: /health includes reaper_alive and reaper_last_sweep_age."""

    @pytest.fixture(autouse=True)
    def set_health_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_HEALTH_SECRET", HEALTH_SECRET)

    async def test_reaper_fields_absent_when_no_cli_pool(self, hub: Hub) -> None:
        """No cli_pool → reaper keys omitted entirely from response."""
        from lyra.bootstrap.health import create_health_app

        assert hub.cli_pool is None
        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        assert "reaper_alive" not in data
        assert "reaper_last_sweep_age" not in data

    async def test_reaper_fields_with_live_cli_pool(self, hub: Hub) -> None:
        """cli_pool with active reaper → reaper_alive=True."""
        from unittest.mock import MagicMock

        from lyra.bootstrap.health import create_health_app
        from lyra.core.cli_pool import CliPool

        cli_pool = CliPool()
        # Simulate a running reaper task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        cli_pool._reaper_task = mock_task
        cli_pool._last_sweep_at = time.monotonic() - 30  # 30s ago
        hub.cli_pool = cli_pool

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["reaper_alive"] is True
        assert data["reaper_last_sweep_age"] is not None
        assert 25 <= data["reaper_last_sweep_age"] <= 35

    async def test_reaper_fields_before_first_sweep(self, hub: Hub) -> None:
        """cli_pool started but no sweep yet → reaper_alive=True, age=None."""
        from unittest.mock import MagicMock

        from lyra.bootstrap.health import create_health_app
        from lyra.core.cli_pool import CliPool

        cli_pool = CliPool()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        cli_pool._reaper_task = mock_task
        cli_pool._last_sweep_at = None
        hub.cli_pool = cli_pool

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["reaper_alive"] is True
        assert data["reaper_last_sweep_age"] is None
