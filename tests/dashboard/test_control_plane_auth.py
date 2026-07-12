"""BFF control-plane auth routes + require_principal (ADR-103 Blocks 1–3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app
from factory.core.auth.control_plane import GlobalRole
from factory.infrastructure.stores.identity.control_plane_store import ControlPlaneStore


@pytest.fixture
async def cp_store(tmp_path: Path):
    store = ControlPlaneStore(tmp_path / "auth.db")
    await store.connect()
    await store.bootstrap_admin_if_empty(
        email="admin@test.local",
        password="admin-secret-99",
        display_name="Admin",
    )
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def client(
    cp_store: ControlPlaneStore, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("FACTORY_DASHBOARD_COOKIE_INSECURE", "1")
    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(
        inbound_bus=bus,
        agent_names=["alpha"],
        port=19998,
    )
    adapter._outbound_listener = MagicMock()
    app = create_app(adapter, control_plane=cp_store)
    return TestClient(app)


class TestAuthPublicAndPrincipal:
    def test_login_sets_cookie_and_me(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        res = client.post(
            "/api/bff/auth/login",
            json={"email": "admin@test.local", "password": "admin-secret-99"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["user"]["email"] == "admin@test.local"
        assert GlobalRole.ADMIN.value in body["principal"]["roles"]
        assert "factory_session" in res.cookies

        me = client.get("/api/bff/auth/me")
        assert me.status_code == 200
        assert me.json()["principal"]["via"] == "session"

    def test_login_bad_password(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        res = client.post(
            "/api/bff/auth/login",
            json={"email": "admin@test.local", "password": "wrong"},
        )
        assert res.status_code == 401

    def test_me_without_auth_401(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        res = client.get("/api/bff/auth/me")
        assert res.status_code == 401

    def test_api_key_bearer(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        login = client.post(
            "/api/bff/auth/login",
            json={"email": "admin@test.local", "password": "admin-secret-99"},
        )
        assert login.status_code == 200
        created = client.post("/api/bff/auth/api-keys", json={"name": "ci"})
        assert created.status_code == 200
        secret = created.json()["secret"]
        assert secret.startswith("fak_")

        # fresh client without cookie
        bus = MagicMock()
        bus.put = AsyncMock()
        adapter = WebAdapter(inbound_bus=bus, agent_names=["alpha"], port=19997)
        adapter._outbound_listener = MagicMock()
        # re-use same store via app from original client
        app = client.app
        bare = TestClient(app)
        me = bare.get(
            "/api/bff/auth/me",
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert me.status_code == 200
        assert me.json()["principal"]["via"] == "api_key"


class TestInviteFlow:
    def test_admin_invite_and_accept(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        assert (
            client.post(
                "/api/bff/auth/login",
                json={"email": "admin@test.local", "password": "admin-secret-99"},
            ).status_code
            == 200
        )
        inv = client.post(
            "/api/bff/auth/invites",
            json={"email": "member@test.local", "ttl_hours": 24},
        )
        assert inv.status_code == 200, inv.text
        token = inv.json()["token"]

        bare = TestClient(client.app)
        accept = bare.post(
            "/api/bff/auth/accept-invite",
            json={
                "token": token,
                "password": "member-secret-1",
                "display_name": "Member",
            },
        )
        assert accept.status_code == 200, accept.text
        assert accept.json()["user"]["email"] == "member@test.local"
        assert accept.json()["user"]["global_role"] == GlobalRole.MEMBER.value

        # open signup path must not exist (404/405 — no register handler)
        assert bare.post("/api/bff/auth/register", json={}).status_code in {404, 405}


class TestE2EBypass:
    def test_e2e_principal_on_me(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        res = client.get("/api/bff/auth/me")
        assert res.status_code == 200
        assert res.json()["principal"]["via"] == "e2e"
