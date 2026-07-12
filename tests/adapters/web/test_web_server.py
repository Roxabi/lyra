"""HTTP tests for the web smoke FastAPI app."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, MagicMock, WebAdapter]:
    monkeypatch.setenv("FACTORY_DASHBOARD_OPERATOR_TOKEN", "test-op-token")
    monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(
        inbound_bus=bus,
        agent_names=["alpha", "beta"],
        port=19999,
    )
    listener = MagicMock()
    listener.cache_inbound = MagicMock()
    adapter._outbound_listener = listener
    app = create_app(adapter)
    tc = TestClient(app)
    tc.headers.update({"Authorization": "Bearer test-op-token"})
    return tc, bus, adapter


class TestWebServer:
    def test_list_agents(
        self, client: tuple[TestClient, MagicMock, WebAdapter]
    ) -> None:
        tc, _, _ = client
        res = tc.get("/api/agents")
        assert res.status_code == 200
        assert res.json()["agents"] == ["alpha", "beta"]

    def test_post_chat_accepts_message(
        self, client: tuple[TestClient, MagicMock, WebAdapter]
    ) -> None:
        tc, bus, _ = client
        res = tc.post(
            "/api/chat",
            json={"agent": "alpha", "text": "hello"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["accepted"] is True
        assert body["session_id"]
        assert body["stream_token"]
        assert bus.put.await_count == 1

    def test_post_chat_rejects_unknown_agent(
        self, client: tuple[TestClient, MagicMock, WebAdapter]
    ) -> None:
        tc, bus, _ = client
        res = tc.post(
            "/api/chat",
            json={"agent": "nope", "text": "hello"},
        )
        assert res.status_code == 400
        assert bus.put.await_count == 0

    def test_stream_requires_token(
        self, client: tuple[TestClient, MagicMock, WebAdapter]
    ) -> None:
        tc, _, _ = client
        res = tc.get("/api/stream/fake-session")
        assert res.status_code == 403

    def test_stream_accepts_valid_token(
        self, client: tuple[TestClient, MagicMock, WebAdapter]
    ) -> None:
        tc, _, adapter = client
        chat = tc.post("/api/chat", json={"agent": "alpha", "text": "hi"})
        body = chat.json()
        adapter.sessions.get_or_create(body["session_id"]).closed = True
        res = tc.get(
            f"/api/stream/{body['session_id']}",
            params={"token": body["stream_token"]},
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers.get("content-type", "")

    def test_bff_agents_status_e2e_stub(
        self,
        client: tuple[TestClient, MagicMock, WebAdapter],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _, _ = client
        res = tc.get("/api/bff/agents/status")
        assert res.status_code == 200
        agents = res.json()["agents"]
        assert len(agents) == 2

    def test_bff_sessions_e2e_stub(
        self,
        client: tuple[TestClient, MagicMock, WebAdapter],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _, _ = client
        res = tc.get("/api/bff/sessions", params={"agent": "alpha"})
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        assert len(sessions) >= 1
        platforms = {s["platform"] for s in sessions}
        assert "web" in platforms

    def test_bff_connectors_fail_closed_without_token(
        self,
        client: tuple[TestClient, MagicMock, WebAdapter],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Block 14: no Tailnet open path when operator token unset."""
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        monkeypatch.delenv("FACTORY_DASHBOARD_OPERATOR_TOKEN", raising=False)
        tc, _, _ = client
        res = tc.get("/api/bff/connectors")
        assert res.status_code == 401

    def test_bff_connectors_e2e_stub(
        self,
        client: tuple[TestClient, MagicMock, WebAdapter],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _, _ = client
        res = tc.get("/api/bff/connectors")
        assert res.status_code == 200
        body = res.json()
        assert len(body["connectors"]) == 2
        assert body["factory_tenant"]

    def test_bff_github_install_url_e2e_stub(
        self,
        client: tuple[TestClient, MagicMock, WebAdapter],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _, _ = client
        res = tc.get("/api/bff/connectors/github/install-url")
        assert res.status_code == 200
        assert "github.com/apps" in res.json()["url"]

    def test_bff_connector_installations_e2e_stub(
        self,
        client: tuple[TestClient, MagicMock, WebAdapter],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _, _ = client
        res = tc.get("/api/bff/connectors/github/installations")
        assert res.status_code == 200
        assert len(res.json()["installations"]) >= 1

    def test_bff_connectors_requires_operator_token_when_configured(
        self,
        client: tuple[TestClient, MagicMock, WebAdapter],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        monkeypatch.setenv("FACTORY_DASHBOARD_OPERATOR_TOKEN", "secret-token")
        tc, _, _ = client
        res = tc.get("/api/bff/connectors")
        assert res.status_code == 401
        res_ok = tc.get(
            "/api/bff/connectors",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert res_ok.status_code == 200

    def test_spa_index_when_dist_built(
        self, client: tuple[TestClient, MagicMock, WebAdapter]
    ) -> None:
        from factory.dashboard.static_mount import dist_available

        if not dist_available():
            pytest.skip("dashboard dist not built")
        tc, _, _ = client
        res = tc.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
