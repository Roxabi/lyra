"""Security audit + rate limit helpers (ADR-103 Blocks 13–14)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app
from factory.dashboard.security import (
    audit_security,
    check_rate_limit,
    client_key,
)
from factory.infrastructure.stores.identity.control_plane_store import ControlPlaneStore


def test_check_rate_limit_allows_then_blocks() -> None:
    key = "test-rate-key-unique"
    # clear any prior
    from factory.dashboard import security as sec

    sec._buckets.pop(key, None)
    for _ in range(5):
        assert check_rate_limit(key, limit=5, window_s=60.0)
    assert not check_rate_limit(key, limit=5, window_s=60.0)


def test_audit_security_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="factory.audit.security"):
        audit_security("login_fail", email="a@b.c", password="secret")
    assert any("factory.audit.security.login_fail" in r.message for r in caplog.records)
    joined = " ".join(r.message for r in caplog.records)
    assert "password" not in joined
    assert "a@b.c" in joined


@pytest.fixture
async def cp_store(tmp_path):
    store = ControlPlaneStore(tmp_path / "auth.db")
    await store.connect()
    await store.bootstrap_admin_if_empty(
        email="admin@test.local",
        password="admin-secret-99",
    )
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def client(cp_store: ControlPlaneStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FACTORY_DASHBOARD_COOKIE_INSECURE", "1")
    monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(inbound_bus=bus, agent_names=["alpha"], port=19990)
    adapter._outbound_listener = MagicMock()
    app = create_app(adapter, control_plane=cp_store)
    return TestClient(app)


def test_login_fail_audited(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="factory.audit.security"):
        res = client.post(
            "/api/bff/auth/login",
            json={"email": "admin@test.local", "password": "wrong"},
        )
    assert res.status_code == 401
    assert any("login_fail" in r.message for r in caplog.records)


def test_login_ok_audited(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="factory.audit.security"):
        res = client.post(
            "/api/bff/auth/login",
            json={"email": "admin@test.local", "password": "admin-secret-99"},
        )
    assert res.status_code == 200
    assert any("login_ok" in r.message for r in caplog.records)


def test_client_key_stable() -> None:
    req = MagicMock()
    req.client.host = "10.0.0.1"
    assert client_key(req, suffix="login") == "10.0.0.1:login"
