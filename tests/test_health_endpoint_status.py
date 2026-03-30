"""Tests for /health endpoint — status, authenticated detail, and hub timestamps.

Covers: issue #111, SC-1, SC-2, SC-3, #207.
Classes: TestHealthUnauthenticated, TestHealthEndpoint, TestHubTimestamps.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.hub import Hub
from lyra.core.message import (
    InboundMessage,
    Platform,
)
from lyra.core.trust import TrustLevel
from tests.conftest import AUTH_HEADERS, HEALTH_SECRET
from tests.core.conftest import push_to_hub

# ---------------------------------------------------------------------------
# T0 — /health unauthenticated returns minimal response (#207)
# ---------------------------------------------------------------------------


class TestHealthUnauthenticated:
    async def test_no_token_returns_ok_only(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#207: Unauthenticated /health returns only {"ok": true}."""
        monkeypatch.delenv("LYRA_HEALTH_SECRET", raising=False)
        from lyra.bootstrap.health import create_health_app

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
        from lyra.bootstrap.health import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/health", headers={"authorization": "Bearer wrong"}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data == {"ok": True}

    async def test_no_secret_configured_returns_ok_only(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#207: When LYRA_HEALTH_SECRET is unset, always minimal."""
        monkeypatch.delenv("LYRA_HEALTH_SECRET", raising=False)
        from lyra.bootstrap.health import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/health", headers={"authorization": "Bearer anything"}
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_empty_secret_env_returns_ok_only(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#207: LYRA_HEALTH_SECRET='' still returns minimal response."""
        monkeypatch.setenv("LYRA_HEALTH_SECRET", "")
        from lyra.bootstrap.health import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers={"authorization": "Bearer "})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# T1 — /health authenticated returns full details
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @pytest.fixture(autouse=True)
    def set_health_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import lyra.bootstrap.health as health_mod

        monkeypatch.setattr(health_mod, "_read_secret", lambda name: HEALTH_SECRET)

    async def test_health_returns_json(self, hub: Hub) -> None:
        """SC-2: /health/detail returns JSON with expected keys."""
        from lyra.bootstrap.health import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

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
        from lyra.bootstrap.health import create_health_app

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
        await push_to_hub(hub, msg)
        await asyncio.sleep(0)  # let feeder task move message to staging

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["queue_size"] == 1

    async def test_health_per_platform_queue_depths(self, hub: Hub) -> None:
        """S2-6: /health/detail reports per-platform queue depths."""
        from unittest.mock import MagicMock

        from lyra.bootstrap.health import create_health_app
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

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
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["queues"]["inbound"]["telegram"] == 1
        assert data["queues"]["outbound"]["telegram"] == 0

    async def test_health_uptime_positive(self, hub: Hub) -> None:
        """SC-2: uptime_s is a positive number."""
        from lyra.bootstrap.health import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["uptime_s"] >= 0

    async def test_health_last_message_age_null_when_no_messages(
        self, hub: Hub
    ) -> None:
        """SC-2: last_message_age_s is null when no messages have been processed."""
        from lyra.bootstrap.health import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["last_message_age_s"] is None

    async def test_health_last_message_age_after_processing(self, hub: Hub) -> None:
        """SC-3: last_message_age_s reflects time since last processed message."""
        from lyra.bootstrap.health import create_health_app

        hub._last_processed_at = time.monotonic()

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["last_message_age_s"] is not None
        assert data["last_message_age_s"] >= 0

    async def test_health_circuits_all_closed(self, hub: Hub) -> None:
        """SC-2: circuits shows state for all registered circuits."""
        from lyra.bootstrap.health import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        circuits = data["circuits"]
        for name in ("anthropic", "telegram", "discord", "hub"):
            assert name in circuits
            assert circuits[name]["state"] == "closed"
            assert circuits[name]["retry_after"] is None

    async def test_health_circuits_shows_open_state(
        self, hub: Hub, circuit_registry: CircuitRegistry
    ) -> None:
        """SC-2: circuits reflects open circuit state."""
        from lyra.bootstrap.health import create_health_app

        cb = circuit_registry.get("anthropic")
        assert cb is not None
        for _ in range(5):
            cb.record_failure()

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["circuits"]["anthropic"]["state"] == "open"
        assert data["circuits"]["anthropic"]["retry_after"] is not None


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
