"""Tests for /health endpoint — status, authenticated detail, and hub timestamps.

Covers: issue #111, SC-1, SC-2, SC-3, #207, #2202.
Classes: TestHealthUnauthenticated, TestHealthEndpoint, TestNatsHealthProbe,
TestHealthReady, TestHubTimestamps.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient

from factory.bootstrap.infra.health import Secrets
from factory.core.auth.trust import TrustLevel
from factory.core.hub import Hub
from factory.core.lifecycle.circuit_breaker import CircuitRegistry
from factory.core.messaging.message import (
    InboundMessage,
    Platform,
    TelegramMeta,
)
from tests.conftest import AUTH_HEADERS, HEALTH_SECRET, yield_once
from tests.core.conftest import push_to_hub

# ---------------------------------------------------------------------------
# T0 — /health unauthenticated returns minimal response (#207)
# ---------------------------------------------------------------------------


class TestHealthUnauthenticated:
    async def test_no_token_returns_ok_only(self, hub: Hub) -> None:
        """#207: Unauthenticated /health returns only {"ok": true}."""
        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, secrets=Mock(spec=Secrets, health_secret=""))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_wrong_token_returns_ok_only(self, hub: Hub) -> None:
        """#207: Wrong Bearer token still returns minimal response."""
        from factory.bootstrap.infra.health import create_health_app

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
        """#207: When no secret is configured, always minimal."""
        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, secrets=Mock(spec=Secrets, health_secret=""))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/health", headers={"authorization": "Bearer anything"}
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_empty_secret_env_returns_ok_only(self, hub: Hub) -> None:
        """#207: Empty health_secret still returns minimal response."""
        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, secrets=Mock(spec=Secrets, health_secret=""))
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
    def set_health_secret(self) -> None:
        self.secrets = Mock(spec=Secrets, health_secret=HEALTH_SECRET)

    async def test_health_returns_json(self, hub: Hub) -> None:
        """SC-2: /health/detail returns JSON with expected keys."""
        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, secrets=self.secrets)
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
        from factory.bootstrap.infra.health import create_health_app

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
            platform_meta=TelegramMeta(chat_id=123),
            trust_level=TrustLevel.TRUSTED,
        )
        await push_to_hub(hub, msg)
        await yield_once()  # let feeder task move message to staging

        app = create_health_app(hub, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["queue_size"] == 1

    async def test_health_per_platform_queue_depths(self, hub: Hub) -> None:
        """S2-6: /health/detail reports per-platform queue depths."""
        from unittest.mock import MagicMock

        from factory.bootstrap.infra.health import create_health_app
        from factory.core.hub.outbound.outbound_dispatcher import OutboundDispatcher

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
            platform_meta=TelegramMeta(chat_id=123),
            trust_level=TrustLevel.TRUSTED,
        )
        await hub.inbound_bus.put(Platform.TELEGRAM, msg)

        app = create_health_app(hub, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["queues"]["inbound"]["telegram"] == 1
        assert data["queues"]["outbound"]["telegram"] == 0

    async def test_health_uptime_positive(self, hub: Hub) -> None:
        """SC-2: uptime_s is a positive number."""
        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["uptime_s"] >= 0

    async def test_health_last_message_age_null_when_no_messages(
        self, hub: Hub
    ) -> None:
        """SC-2: last_message_age_s is null when no messages have been processed."""
        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["last_message_age_s"] is None

    async def test_health_last_message_age_after_processing(self, hub: Hub) -> None:
        """SC-3: last_message_age_s reflects time since last processed message."""
        from factory.bootstrap.infra.health import create_health_app

        hub._outbound_router._last_processed_at = time.monotonic()

        app = create_health_app(hub, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["last_message_age_s"] is not None
        assert data["last_message_age_s"] >= 0

    async def test_health_circuits_all_closed(self, hub: Hub) -> None:
        """SC-2: circuits shows state for all registered circuits."""
        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        circuits = data["circuits"]
        for name in ("claude-cli", "telegram", "discord", "hub"):
            assert name in circuits
            assert circuits[name]["state"] == "closed"
            assert circuits[name]["retry_after"] is None

    async def test_health_circuits_shows_open_state(
        self, hub: Hub, circuit_registry: CircuitRegistry
    ) -> None:
        """SC-2: circuits reflects open circuit state."""
        from factory.bootstrap.infra.health import create_health_app

        cb = circuit_registry.get("claude-cli")
        assert cb is not None
        for _ in range(5):
            cb.record_failure()

        app = create_health_app(hub, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["circuits"]["claude-cli"]["state"] == "open"
        assert data["circuits"]["claude-cli"]["retry_after"] is not None


# ---------------------------------------------------------------------------
# T1b — NATS health probe (#449)
# ---------------------------------------------------------------------------


class TestNatsHealthProbe:
    """#449: /health/detail surfaces NATS status only when NATS is configured."""

    @pytest.fixture(autouse=True)
    def set_health_secret(self) -> None:
        self.secrets = Mock(spec=Secrets, health_secret=HEALTH_SECRET)

    async def test_nats_field_absent_when_url_unset(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No NATS_URL → no `nats` and no `status` keys in the response."""
        monkeypatch.delenv("NATS_URL", raising=False)
        from factory.bootstrap.infra.health import create_health_app

        # nc omitted — mirrors unified mode
        app = create_health_app(hub, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert "nats" not in data
        assert "status" not in data
        assert data["ok"] is True

    async def test_nats_ok_when_url_set_and_connected(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NATS_URL set + nc.is_connected → `nats: ok` and `status: ok`."""
        from unittest.mock import MagicMock

        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

        nc = MagicMock()
        nc.is_connected = True

        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, nc=nc, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["nats"] == "ok"
        assert data["status"] == "ok"

    async def test_nats_unreachable_when_url_set_and_disconnected(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NATS_URL set + nc disconnected → `nats: unreachable` + degraded."""
        from unittest.mock import MagicMock

        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

        nc = MagicMock()
        nc.is_connected = False

        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, nc=nc, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["nats"] == "unreachable"
        assert data["status"] == "degraded"

    async def test_nats_unreachable_when_nc_none_but_url_set(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NATS_URL set but nc=None (caller didn't wire it) → unreachable."""
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, secrets=self.secrets)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["nats"] == "unreachable"
        assert data["status"] == "degraded"

    async def test_nats_unreachable_when_is_connected_raises(
        self,
        hub: Hub,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """#449 edge: `nc.is_connected` raises AttributeError -> unreachable + DEBUG."""
        import logging as _logging

        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")

        # Simulate a wrong-type nc object whose is_connected property raises
        # AttributeError (e.g. a stub/mock that does not implement the attribute).
        class _BadNc:
            @property
            def is_connected(self) -> bool:
                raise AttributeError("boom")

        nc = _BadNc()

        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub, nc=nc, secrets=self.secrets)
        transport = ASGITransport(app=app)

        with caplog.at_level(_logging.DEBUG, logger="factory.bootstrap.infra.health"):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.get("/health/detail", headers=AUTH_HEADERS)

        data = resp.json()
        assert data["nats"] == "unreachable"
        assert data["status"] == "degraded"
        assert any(
            "_probe_nats" in r.getMessage()
            and r.name == "factory.bootstrap.infra.health"
            and r.levelno == _logging.DEBUG
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# T1c — /health/ready NATS/JetStream round-trip probe (#2202)
# ---------------------------------------------------------------------------


class TestHealthReady:
    """#2202: /health/ready round-trips kv.get('hub.ready') on factory-state.

    Unlike /health/detail, this endpoint requires no auth header (mirrors
    /health) and is a liveness-independent readiness signal.
    """

    async def test_ready_no_auth_required(self, hub: Hub) -> None:
        """/health/ready is unauthenticated, like /health (not /health/detail)."""
        from factory.bootstrap.infra.health import create_health_app

        nc = MagicMock()
        nc.jetstream.return_value.key_value = AsyncMock(
            return_value=MagicMock(get=AsyncMock(return_value=MagicMock(value=b"true")))
        )

        app = create_health_app(hub, nc=nc)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")

        assert resp.status_code == 200

    async def test_ready_true_when_kv_round_trip_succeeds(self, hub: Hub) -> None:
        """200 + ready:true when hub.ready == b'true' in factory-state KV."""
        from factory.bootstrap.infra.health import create_health_app

        entry = MagicMock(value=b"true")
        kv = MagicMock(get=AsyncMock(return_value=entry))
        nc = MagicMock()
        nc.jetstream.return_value.key_value = AsyncMock(return_value=kv)

        app = create_health_app(hub, nc=nc)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")

        assert resp.status_code == 200
        assert resp.json() == {"ready": True, "reason": None}

    async def test_ready_false_when_nc_not_configured(self, hub: Hub) -> None:
        """503 + reason when no NATS client was wired (nc=None)."""
        from factory.bootstrap.infra.health import create_health_app

        app = create_health_app(hub)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")

        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False
        assert data["reason"] == "nats client not configured"

    async def test_ready_false_when_bucket_not_found(self, hub: Hub) -> None:
        """503 when the factory-state KV bucket has not been provisioned."""
        from nats.js.errors import BucketNotFoundError

        from factory.bootstrap.infra.health import create_health_app

        nc = MagicMock()
        nc.jetstream.return_value.key_value = AsyncMock(side_effect=BucketNotFoundError)

        app = create_health_app(hub, nc=nc)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")

        assert resp.status_code == 503
        assert resp.json() == {
            "ready": False,
            "reason": "factory-state bucket not provisioned",
        }

    async def test_ready_false_when_key_not_found(self, hub: Hub) -> None:
        """503 when the hub.ready key is missing from the KV bucket."""
        from nats.js.errors import KeyNotFoundError

        from factory.bootstrap.infra.health import create_health_app

        kv = MagicMock(get=AsyncMock(side_effect=KeyNotFoundError))
        nc = MagicMock()
        nc.jetstream.return_value.key_value = AsyncMock(return_value=kv)

        app = create_health_app(hub, nc=nc)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")

        assert resp.status_code == 503
        assert resp.json() == {"ready": False, "reason": "hub.ready key not found"}

    async def test_ready_false_when_value_not_true(self, hub: Hub) -> None:
        """503 when the KV entry exists but does not hold b'true' (defensive)."""
        from factory.bootstrap.infra.health import create_health_app

        entry = MagicMock(value=b"false")
        kv = MagicMock(get=AsyncMock(return_value=entry))
        nc = MagicMock()
        nc.jetstream.return_value.key_value = AsyncMock(return_value=kv)

        app = create_health_app(hub, nc=nc)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")

        assert resp.status_code == 503
        assert resp.json() == {"ready": False, "reason": "hub.ready value unexpected"}

    async def test_ready_false_on_nats_error(self, hub: Hub) -> None:
        """503 when the round-trip raises a generic nats.errors.Error."""
        import nats.errors

        from factory.bootstrap.infra.health import create_health_app

        nc = MagicMock()
        nc.jetstream.return_value.key_value = AsyncMock(
            side_effect=nats.errors.Error("no responders")
        )

        app = create_health_app(hub, nc=nc)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")

        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False
        assert "no responders" in data["reason"]

    async def test_ready_false_on_timeout(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """503 when the round-trip exceeds the bounded probe timeout.

        Timeout shrunk to keep the test fast — the probe must not block the
        HTTP handler on a wedged JetStream backend (#2202: this is exactly
        the April-incident failure mode _probe_nats couldn't detect).
        """
        import factory.bootstrap.infra.health as health_module
        from factory.bootstrap.infra.health import create_health_app

        monkeypatch.setattr(health_module, "_READY_TIMEOUT_S", 0.05)

        async def _hang(*_args: object, **_kwargs: object) -> None:
            # Simulates a wedged coroutine for asyncio.timeout() to cancel.
            await asyncio.sleep(10)  # event-based

        nc = MagicMock()
        nc.jetstream.return_value.key_value = AsyncMock(side_effect=_hang)

        app = create_health_app(hub, nc=nc)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")

        assert resp.status_code == 503
        assert resp.json() == {"ready": False, "reason": "nats round-trip timed out"}

    async def test_ready_false_on_unexpected_error_logs_exception(
        self, hub: Hub, caplog: pytest.LogCaptureFixture
    ) -> None:
        """503 + log.exception on an unanticipated error (graceful degradation)."""
        from factory.bootstrap.infra.health import create_health_app

        nc = MagicMock()
        nc.jetstream.return_value.key_value = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        app = create_health_app(hub, nc=nc)
        transport = ASGITransport(app=app)
        with caplog.at_level(logging.ERROR, logger="factory.bootstrap.infra.health"):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.get("/health/ready")

        assert resp.status_code == 503
        assert resp.json() == {"ready": False, "reason": "unexpected error"}
        assert any(
            "_probe_nats_ready" in r.getMessage() and r.levelno == logging.ERROR
            for r in caplog.records
        )


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
