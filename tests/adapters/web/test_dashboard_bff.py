"""BFF integration tests on the real adapter→hub_client path (#1771)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
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

    def test_bff_spans_returns_indexed_json(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, _nc = wired_client
        jsonl = tmp_path / "spans.jsonl"
        db = tmp_path / "otel-raw.db"
        job_id = "c" * 32
        line = {
            "trace_id": "trace-bff",
            "span_id": "span-bff",
            "name": "nats.work",
            "start_time_unix_nano": 1_000_000_000,
            "end_time_unix_nano": 2_000_000_000,
            "attributes": {
                "roxabi.job_id": job_id,
                "roxabi.pool_id": "pool-bff",
                "roxabi.component": "clipool-workers",
            },
        }
        jsonl.write_text(json.dumps(line) + "\n", encoding="utf-8")
        monkeypatch.setenv("FACTORY_OTEL_JSONL_PATH", str(jsonl))
        monkeypatch.setenv("FACTORY_OTEL_RAW_DB", str(db))

        res = tc.get(
            f"/api/bff/spans?job_id={job_id}&component=clipool-workers&page_size=10"
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 1
        assert body["page"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["job_id"] == job_id
        assert body["items"][0]["component"] == "clipool-workers"

    def test_bff_spans_quadlet_volume_layout(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mirrors factory-dashboard.container: ro JSONL dir + rw SQLite file mount."""
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, _nc = wired_client
        otel_data = tmp_path / "otel-data"
        otel_index = tmp_path / "otel-index"
        otel_data.mkdir()
        otel_index.mkdir()
        jsonl = otel_data / "spans.jsonl"
        db = otel_index / "otel-raw.db"
        job_id = "e" * 32
        line = {
            "trace_id": "trace-quadlet",
            "span_id": "span-quadlet",
            "name": "nats.work",
            "start_time_unix_nano": 1_000_000_000,
            "end_time_unix_nano": 2_000_000_000,
            "attributes": {
                "roxabi.job_id": job_id,
                "roxabi.pool_id": "pool-quadlet",
                "roxabi.component": "clipool-workers",
            },
        }
        jsonl.write_text(json.dumps(line) + "\n", encoding="utf-8")
        monkeypatch.setenv("FACTORY_OTEL_JSONL_PATH", "/otel-data/spans.jsonl")
        monkeypatch.setenv("FACTORY_OTEL_RAW_DB", "/otel-index/otel-raw.db")
        monkeypatch.setattr(
            "factory.dashboard.otel_raw_reader._default_jsonl_path",
            lambda: jsonl,
        )
        monkeypatch.setattr(
            "factory.dashboard.otel_raw_reader._default_db_path",
            lambda: db,
        )

        res = tc.get(
            f"/api/bff/spans?job_id={job_id}&component=clipool-workers&page_size=10"
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 1
        assert db.exists()
        assert body["items"][0]["job_id"] == job_id

    def test_bff_spans_degrades_on_readonly_db(
        self,
        wired_client: tuple[TestClient, WebAdapter, AsyncMock],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        tc, _adapter, _nc = wired_client
        otel_data = tmp_path / "otel-data"
        otel_index = tmp_path / "otel-index"
        otel_data.mkdir()
        otel_index.mkdir()
        jsonl = otel_data / "spans.jsonl"
        db = otel_index / "otel-raw.db"
        jsonl.write_text(
            json.dumps(
                {
                    "trace_id": "trace-ro",
                    "span_id": "span-ro",
                    "name": "nats.work",
                    "start_time_unix_nano": 1_000_000_000,
                    "end_time_unix_nano": 2_000_000_000,
                    "attributes": {
                        "roxabi.job_id": "f" * 32,
                        "roxabi.component": "omp-workers",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        db.touch()
        os.chmod(db, stat.S_IRUSR | stat.S_IRGRP)
        os.chmod(otel_index, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
        monkeypatch.setenv("FACTORY_OTEL_JSONL_PATH", "/otel-data/spans.jsonl")
        monkeypatch.setenv("FACTORY_OTEL_RAW_DB", "/otel-index/otel-raw.db")
        monkeypatch.setattr(
            "factory.dashboard.otel_raw_reader._default_jsonl_path",
            lambda: jsonl,
        )
        monkeypatch.setattr(
            "factory.dashboard.otel_raw_reader._default_db_path",
            lambda: db,
        )
        try:
            res = tc.get("/api/bff/spans?job_id=" + "f" * 32)
        finally:
            os.chmod(otel_index, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
            os.chmod(db, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
        assert res.status_code == 200
        assert res.json()["total"] == 0
        assert res.json()["items"] == []
