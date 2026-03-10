"""Tests for /health endpoint (issue #111, SC-1, SC-2, SC-3)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub
from lyra.core.message import (
    Message,
    MessageType,
    Platform,
    TelegramContext,
    TextContent,
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


@pytest.fixture()
def hub(circuit_registry: CircuitRegistry) -> Hub:
    return Hub(circuit_registry=circuit_registry)


# ---------------------------------------------------------------------------
# T1 — /health returns correct JSON shape
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_health_returns_json(self, hub: Hub) -> None:
        """SC-2: /health returns JSON with expected keys."""
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert "queue_size" in data
        assert "last_message_age_s" in data
        assert "uptime_s" in data
        assert "circuits" in data

    async def test_health_queue_size_reflects_bus(self, hub: Hub) -> None:
        """SC-2: queue_size reflects actual bus queue size."""
        from lyra.__main__ import create_health_app

        # Put a message on the bus to increase queue size
        msg = Message.from_adapter(
            platform=Platform.TELEGRAM,
            bot_id="main",
            user_id="test",
            user_name="test",
            content=TextContent(text="hello"),
            type=MessageType.TEXT,
            timestamp=datetime.now(timezone.utc),
            platform_context=TelegramContext(chat_id=123),
        )
        await hub.bus.put(msg)

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        data = resp.json()
        assert data["queue_size"] == 1

    async def test_health_uptime_positive(self, hub: Hub) -> None:
        """SC-2: uptime_s is a positive number."""
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

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
            resp = await client.get("/health")

        data = resp.json()
        assert data["last_message_age_s"] is None

    async def test_health_last_message_age_after_processing(self, hub: Hub) -> None:
        """SC-3: last_message_age_s reflects time since last processed message."""
        from lyra.__main__ import create_health_app

        # Simulate a processed message by setting _last_processed_at
        hub._last_processed_at = time.monotonic()

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        data = resp.json()
        assert data["last_message_age_s"] is not None
        assert data["last_message_age_s"] >= 0

    async def test_health_circuits_all_closed(self, hub: Hub) -> None:
        """SC-2: circuits shows state for all registered circuits."""
        from lyra.__main__ import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

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

        # Trip the anthropic circuit
        cb = circuit_registry.get("anthropic")
        assert cb is not None
        for _ in range(5):
            cb.record_failure()

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

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
