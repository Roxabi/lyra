"""Tests for /health endpoint (issue #111, SC-1, SC-2, SC-3, #207)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from lyra.core.auth import TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub
from lyra.core.message import (
    InboundMessage,
    Platform,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def circuit_registry() -> CircuitRegistry:
    registry = CircuitRegistry()
    for name in ("anthropic", "telegram", "discord", "hub"):
        registry.register(CircuitBreaker(name=name))
    return registry


HEALTH_SECRET = "test-health-secret"
AUTH_HEADERS = {"authorization": f"Bearer {HEALTH_SECRET}"}


@pytest.fixture()
def hub(circuit_registry: CircuitRegistry) -> Hub:
    return Hub(circuit_registry=circuit_registry)


# ---------------------------------------------------------------------------
# T0 — /health unauthenticated returns minimal response (#207)
# ---------------------------------------------------------------------------


class TestHealthUnauthenticated:
    async def test_no_token_returns_ok_only(self, hub: Hub) -> None:
        """#207: Unauthenticated /health returns only {"ok": true}."""
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_wrong_token_returns_ok_only(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#207: Wrong Bearer token still returns minimal response."""
        monkeypatch.setenv("LYRA_HEALTH_SECRET", HEALTH_SECRET)
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/health", headers={"authorization": "Bearer wrong"}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data == {"ok": True}

    async def test_no_secret_configured_returns_ok_only(self, hub: Hub) -> None:
        """#207: When LYRA_HEALTH_SECRET is unset, always minimal."""
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/health", headers={"authorization": "Bearer anything"}
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# T1 — /health authenticated returns full details
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @pytest.fixture(autouse=True)
    def _set_health_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_HEALTH_SECRET", HEALTH_SECRET)

    async def test_health_returns_json(self, hub: Hub) -> None:
        """SC-2: /health returns JSON with expected keys."""
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "queue_size" in data
        assert "queues" in data
        assert "inbound" in data["queues"]
        assert "outbound" in data["queues"]
        assert "last_message_age_s" in data
        assert "uptime_s" in data
        assert "circuits" in data

    async def test_health_queue_size_reflects_staging(self, hub: Hub) -> None:
        """SC-2: queue_size reflects the staging queue depth."""
        from lyra.__main__ import create_health_app

        msg = InboundMessage(
            id="msg-health-1",
            platform="telegram",
            bot_id="main",
            user_id="test",
            user_name="test",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            scope_id="chat:123",
            platform_meta={
                "chat_id": 123,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            },
            trust_level=TrustLevel.TRUSTED,
        )
        await hub.bus.put(msg)

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["queue_size"] == 1

    async def test_health_per_platform_queue_depths(self, hub: Hub) -> None:
        """S2-6: /health reports per-platform queue depths."""
        from unittest.mock import MagicMock

        from lyra.__main__ import create_health_app
        from lyra.core.outbound_dispatcher import OutboundDispatcher

        hub.register_adapter(Platform.TELEGRAM, "main", MagicMock())
        tg_dispatcher = OutboundDispatcher(
            platform_name="telegram",
            adapter=MagicMock(),
        )
        hub.register_outbound_dispatcher(Platform.TELEGRAM, "main", tg_dispatcher)

        msg = InboundMessage(
            id="msg-health-2",
            platform="telegram",
            bot_id="main",
            user_id="test",
            user_name="test",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            scope_id="chat:123",
            platform_meta={
                "chat_id": 123,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            },
            trust_level=TrustLevel.TRUSTED,
        )
        hub.inbound_bus.put(Platform.TELEGRAM, msg)

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["queues"]["inbound"]["telegram"] == 1
        assert data["queues"]["outbound"]["telegram"] == 0

    async def test_health_uptime_positive(self, hub: Hub) -> None:
        """SC-2: uptime_s is a positive number."""
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["uptime_s"] >= 0

    async def test_health_last_message_age_null_when_no_messages(
        self, hub: Hub
    ) -> None:
        """SC-2: last_message_age_s is null when no messages have been processed."""
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["last_message_age_s"] is None

    async def test_health_last_message_age_after_processing(self, hub: Hub) -> None:
        """SC-3: last_message_age_s reflects time since last processed message."""
        from lyra.__main__ import create_health_app

        hub._last_processed_at = time.monotonic()

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["last_message_age_s"] is not None
        assert data["last_message_age_s"] >= 0

    async def test_health_circuits_all_closed(self, hub: Hub) -> None:
        """SC-2: circuits shows state for all registered circuits."""
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        circuits = data["circuits"]
        for name in ("anthropic", "telegram", "discord", "hub"):
            assert name in circuits
            assert circuits[name]["state"] == "closed"

    async def test_health_circuits_shows_open_state(
        self, hub: Hub, circuit_registry: CircuitRegistry
    ) -> None:
        """SC-2: circuits reflects open circuit state."""
        from lyra.__main__ import create_health_app

        cb = circuit_registry.get("anthropic")
        assert cb is not None
        for _ in range(5):
            cb.record_failure()

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["circuits"]["anthropic"]["state"] == "open"


# ---------------------------------------------------------------------------
# T2 — Hub tracks _last_processed_at and _start_time
# ---------------------------------------------------------------------------


class TestHubTimestamps:
    def test_hub_has_start_time(self, hub: Hub) -> None:
        """SC-3: Hub sets _start_time on init."""
        assert hasattr(hub, "_start_time")
        assert isinstance(hub._start_time, float)

    def test_hub_has_last_processed_at_none(self, hub: Hub) -> None:
        """SC-3: Hub._last_processed_at is None initially."""
        assert hasattr(hub, "_last_processed_at")
        assert hub._last_processed_at is None


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

        from lyra.__main__ import create_health_app
        from lyra.agents.anthropic_agent import AnthropicAgent
        from lyra.core.agent import Agent, ModelConfig
        from lyra.core.runtime_config import RuntimeConfig
        from lyra.llm.base import LlmResult

        config = Agent(
            name="lyra_default",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
            model_config=ModelConfig(
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
        from lyra.__main__ import create_health_app

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
