"""BFF integration tests on the real adapter→hub_client path (#1771)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app
from factory.core.messaging.message import WebMeta
from roxabi_contracts.dashboard import SUBJECTS


@pytest.fixture
def wired_client() -> tuple[TestClient, WebAdapter, AsyncMock]:
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
    nc = AsyncMock()
    adapter.set_nats_client(nc)
    app = create_app(adapter)
    return TestClient(app), adapter, nc


def _rpc_response(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(payload).encode()
    return msg


class TestDashboardBffRealPath:
    def test_hub_client_reads_nats_wired_before_astart(
        self, wired_client: tuple[TestClient, WebAdapter, AsyncMock]
    ) -> None:
        """Prod path: nc must be visible to BFF after set_nats_client pre-astart."""
        _tc, adapter, nc = wired_client
        from factory.dashboard.hub_client import DashboardHubClient

        hub = DashboardHubClient(adapter)
        assert hub._nc() is nc

    def test_agents_status_calls_hub_rpc_without_e2e(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {
                    "agents": [
                        {
                            "agent": "alpha",
                            "in_roster": True,
                            "harness": "claude-cli",
                            "harness_reachable": True,
                            "online": True,
                        },
                        {
                            "agent": "beta",
                            "in_roster": True,
                            "harness": "claude-cli",
                            "harness_reachable": False,
                            "online": False,
                        },
                    ]
                }
            )
        )
        res = tc.get("/api/bff/agents/status")
        assert res.status_code == 200
        nc.request.assert_awaited_once()
        subject = nc.request.await_args.args[0]
        assert subject == SUBJECTS.agents_status

    def test_post_chat_forwards_harness_and_model(
        self, wired_client: tuple[TestClient, WebAdapter, AsyncMock]
    ) -> None:
        tc, adapter, _nc = wired_client
        res = tc.post(
            "/api/chat",
            json={
                "agent": "alpha",
                "text": "hello",
                "harness": "omp-rpc",
                "model": "omp-fast",
            },
        )
        assert res.status_code == 200
        msg = adapter.normalize(
            {
                "agent": "alpha",
                "text": "hello",
                "harness": "omp-rpc",
                "model": "omp-fast",
            }
        )
        assert isinstance(msg.platform_meta, WebMeta)
        assert msg.platform_meta.harness == "omp-rpc"
        assert msg.platform_meta.model == "omp-fast"

    def test_agents_status_passes_harness_query_to_hub(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {
                    "agents": [
                        {
                            "agent": "alpha",
                            "in_roster": True,
                            "harness": "omp-rpc",
                            "harness_reachable": True,
                            "online": True,
                        }
                    ]
                }
            )
        )
        res = tc.get(
            "/api/bff/agents/status",
            params={"agent": "alpha", "harness": "omp-rpc"},
        )
        assert res.status_code == 200
        payload = json.loads(nc.request.await_args.args[1].decode())
        assert payload["harness_by_agent"] == {"alpha": "omp-rpc"}

    def test_list_sessions_calls_hub_rpc_without_e2e(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {
                    "sessions": [
                        {
                            "session_id": "hub-sess-1",
                            "pool_id": "web:smoke:agent:alpha",
                            "platform": "web",
                            "cli_session_id": "cli-hub-1",
                            "first_user_msg": "from hub",
                            "turn_count": 3,
                            "last_active_at": "2026-06-28T10:00:00Z",
                        }
                    ]
                }
            )
        )
        res = tc.get("/api/bff/sessions", params={"agent": "alpha"})
        assert res.status_code == 200
        body = res.json()
        assert body["sessions"][0]["session_id"] == "hub-sess-1"
        assert body["sessions"][0]["first_user_msg"] == "from hub"
        assert nc.request.await_args.args[0] == SUBJECTS.sessions_list

    def test_resume_session_calls_hub_rpc_without_e2e(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {"accepted": True, "message": "resumed cli-hub-9"}
            )
        )
        res = tc.post(
            "/api/bff/sessions/resume",
            json={"agent": "alpha", "cli_session_id": "cli-hub-9"},
        )
        assert res.status_code == 200
        assert res.json()["accepted"] is True
        assert nc.request.await_args.args[0] == SUBJECTS.sessions_resume

    def test_ops_health_e2e_stub(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _adapter, _nc = wired_client
        res = tc.get("/api/bff/ops/health")
        assert res.status_code == 200
        body = res.json()
        assert len(body["engines"]) == 3
        assert body["engines"][0]["engine"] == "loki"

    def test_ops_logs_e2e_stub(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _adapter, _nc = wired_client
        res = tc.get("/api/bff/ops/logs", params={"preset": "hub-errors"})
        assert res.status_code == 200
        body = res.json()
        assert body["preset"] == "hub-errors"
        assert len(body["entries"]) >= 1
