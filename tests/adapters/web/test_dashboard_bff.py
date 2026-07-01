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

    def test_jobs_launch_e2e_stub(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _adapter, _nc = wired_client
        res = tc.post(
            "/api/bff/jobs/launch",
            json={"agent": "alpha", "prompt": "operator task", "job_name": "omp"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["accepted"] is True
        assert body["job_id"] == "e2e-launch-1"

    def test_fleet_list_calls_hub_rpc(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {
                    "rows": [
                        {
                            "container_name": "factory-hub",
                            "host": "roxabituwer",
                            "component_key": "hub",
                            "image_ref": "ghcr.io/roxabi/factory:staging-svc",
                            "image_revision": "abc",
                            "health": "healthy",
                            "status": "ok",
                            "last_report_at": "2026-06-29T12:00:00+00:00",
                            "age_s": 5.0,
                            "systemd_unit": "factory-hub.service",
                            "instrumented": True,
                            "source": "live",
                        }
                    ]
                }
            )
        )
        res = tc.get("/api/bff/fleet")
        assert res.status_code == 200
        body = res.json()
        assert body["rows"][0]["container_name"] == "factory-hub"
        nc.request.assert_awaited_once()
        assert nc.request.await_args.args[0] == SUBJECTS.fleet_list

    def test_fleet_list_e2e_stub(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _adapter, _nc = wired_client
        res = tc.get("/api/bff/fleet")
        assert res.status_code == 200
        rows = res.json()["rows"]
        assert any(r["container_name"] == "factory-hub" for r in rows)
        assert any(r["status"] == "unknown" for r in rows)

    def test_jobs_steer_e2e_stub(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_E2E", "1")
        tc, _adapter, _nc = wired_client
        res = tc.post(
            "/api/bff/jobs/steer",
            json={"job_id": "job-xyz", "text": "nudge left"},
        )
        assert res.status_code == 200
        assert res.json()["accepted"] is True

    def test_list_agents_config_calls_hub_rpc(
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
                            "name": "alpha",
                            "backend": "claude-cli",
                            "model": "sonnet",
                            "updated_at": "2026-06-29T10:00:00Z",
                            "soul_document_bytes": 512,
                            "has_soul": True,
                        }
                    ]
                }
            )
        )
        res = tc.get("/api/bff/agents")
        assert res.status_code == 200
        assert res.json()["agents"][0]["name"] == "alpha"
        assert nc.request.await_args.args[0] == SUBJECTS.agents_list

    def test_get_agent_config_calls_hub_rpc(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {
                    "name": "alpha",
                    "backend": "omp-rpc",
                    "model": "grok-4-fast",
                    "voice_json": None,
                    "soul_meta_json": {"header": {"display_name": "Alpha"}},
                    "soul_document_blob_ref": "sha256:abc",
                    "soul_document_bytes": 900,
                    "updated_at": "2026-06-29T10:00:00Z",
                }
            )
        )
        res = tc.get("/api/bff/agents/alpha")
        assert res.status_code == 200
        assert res.json()["soul_document_blob_ref"] == "sha256:abc"
        assert nc.request.await_args.args[0] == SUBJECTS.agents_get

    def test_create_agent_config_calls_hub_rpc(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {
                    "name": "nova",
                    "backend": "claude-cli",
                    "model": "sonnet",
                    "voice_json": None,
                    "soul_meta_json": {"header": {"display_name": "Nova"}},
                    "soul_document_blob_ref": None,
                    "soul_document_bytes": None,
                    "updated_at": "2026-06-30T10:00:00Z",
                }
            )
        )
        res = tc.post(
            "/api/bff/agents",
            json={
                "name": "nova",
                "backend": "claude-cli",
                "model": "sonnet",
                "display_name": "Nova",
            },
        )
        assert res.status_code == 200
        assert res.json()["name"] == "nova"
        assert nc.request.await_args.args[0] == SUBJECTS.agents_create

    def test_admin_access_calls_hub_rpc(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {
                    "users": [
                        {
                            "user_id": "rx:user:abc",
                            "display_name": "Ops",
                            "telegram": {
                                "platform": "telegram",
                                "platform_uid": "123",
                                "platform_key": "tg:user:123",
                            },
                            "discord": None,
                            "agents": ["alpha"],
                        }
                    ]
                }
            )
        )
        res = tc.get("/api/bff/admin/access")
        assert res.status_code == 200
        assert res.json()["users"][0]["user_id"] == "rx:user:abc"
        assert nc.request.await_args.args[0] == SUBJECTS.admin_access

    def test_create_admin_user_calls_hub_rpc(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {
                    "user_id": "rx:user:new1",
                    "display_name": "Nova",
                    "email": "nova@example.com",
                    "telegram": None,
                    "discord": None,
                    "agents": [],
                }
            )
        )
        res = tc.post(
            "/api/bff/admin/users",
            json={"display_name": "Nova", "email": "nova@example.com"},
        )
        assert res.status_code == 200
        assert res.json()["email"] == "nova@example.com"
        assert nc.request.await_args.args[0] == SUBJECTS.admin_user_create

    def test_patch_admin_user_calls_hub_rpc(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, nc = wired_client
        nc.request = AsyncMock(
            return_value=_rpc_response(
                {
                    "user_id": "rx:user:abc",
                    "display_name": "Ops",
                    "email": "ops@example.com",
                    "telegram": None,
                    "discord": None,
                    "agents": ["alpha"],
                }
            )
        )
        res = tc.patch(
            "/api/bff/admin/users/rx:user:abc",
            json={"display_name": "Ops", "email": "ops@example.com"},
        )
        assert res.status_code == 200
        assert res.json()["display_name"] == "Ops"
        assert nc.request.await_args.args[0] == SUBJECTS.admin_user_patch

    def test_bff_spans_proxies_factory_otel(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, _nc = wired_client
        job_id = "c" * 32

        async def _fake_fetch(**kwargs: object) -> dict:
            assert kwargs["job_id"] == job_id
            assert kwargs["component"] == "clipool-workers"
            return {
                "items": [
                    {
                        "trace_id": "trace-bff",
                        "span_id": "span-bff",
                        "job_id": job_id,
                        "pool_id": "pool-bff",
                        "component": "clipool-workers",
                        "envelope_name": None,
                        "subject": None,
                        "name": "nats.work",
                        "start_ts": 1.0,
                        "duration_ms": 1.0,
                        "attributes": {},
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 10,
            }

        monkeypatch.setattr("factory.dashboard.routes.bff.fetch_spans", _fake_fetch)
        res = tc.get(
            f"/api/bff/spans?job_id={job_id}&component=clipool-workers&page_size=10"
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 1
        assert body["items"][0]["job_id"] == job_id
