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