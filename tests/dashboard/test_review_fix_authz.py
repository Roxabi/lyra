"""Regression tests for PR #2293 review blockers (principal + admin + bootstrap)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app
from factory.bootstrap.factory.dashboard.admin_rpc import (
    handle_admin_access,
    handle_admin_user_create,
)
from factory.bootstrap.factory.dashboard_agents_rpc import _admin_mutate_gate
from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole
from factory.core.auth.control_plane_wire import (
    clear_request_principal,
    set_request_principal,
)
from factory.infrastructure.stores.identity.control_plane_store import (
    open_control_plane_store,
)


@pytest.fixture
def no_auth_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
    monkeypatch.delenv("FACTORY_DASHBOARD_OPERATOR_TOKEN", raising=False)
    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(inbound_bus=bus, agent_names=["alpha"], port=19998)
    adapter.set_nats_client(AsyncMock())
    return TestClient(create_app(adapter))


class TestBffRequirePrincipal:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/bff/jobs"),
            ("get", "/api/bff/agents"),
            ("get", "/api/bff/admin/access"),
            ("get", "/api/bff/ops/logs"),
            ("get", "/api/bff/spans"),
            ("post", "/api/bff/jobs/stream-token"),
            ("post", "/api/bff/pipeline/stream-token"),
        ],
    )
    def test_sensitive_routes_401_without_auth(
        self, no_auth_client: TestClient, method: str, path: str
    ) -> None:
        res = getattr(no_auth_client, method)(path)
        assert res.status_code == 401, (path, res.status_code, res.text)


class TestAdminRpcAuthz:
    @pytest.mark.asyncio
    async def test_member_denied_admin_access(self) -> None:
        clear_request_principal()
        set_request_principal(
            ControlPlanePrincipal(
                user_id="rx:member",
                roles=frozenset({GlobalRole.MEMBER.value}),
                org_ids=frozenset(),
                active_org_id=None,
                via="session",
            )
        )
        try:
            hub = MagicMock()
            out = await handle_admin_access(hub, MagicMock(), {})
            assert out.get("error") == "forbidden"
        finally:
            clear_request_principal()

    @pytest.mark.asyncio
    async def test_member_denied_user_create(self) -> None:
        clear_request_principal()
        set_request_principal(
            ControlPlanePrincipal(
                user_id="rx:member",
                roles=frozenset({GlobalRole.MEMBER.value}),
                org_ids=frozenset(),
                active_org_id=None,
                via="session",
            )
        )
        try:
            hub = MagicMock()
            out = await handle_admin_user_create(
                hub,
                MagicMock(),
                {
                    "body": {
                        "display_name": "x",
                        "email": "x@y.com",
                    }
                },
            )
            assert out.get("error") == "forbidden"
        finally:
            clear_request_principal()

    def test_agent_mutate_gate_fail_closed_without_principal(self) -> None:
        clear_request_principal()
        denied = _admin_mutate_gate()
        assert denied is not None
        assert denied["error"] == "unauthorized"


class TestBootstrapPassword:
    @pytest.mark.asyncio
    async def test_email_without_password_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_EMAIL", "a@x.com")
        monkeypatch.delenv("FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
        monkeypatch.setenv("FACTORY_AUTH_DB", str(tmp_path / "auth.db"))
        with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_PASSWORD"):
            await open_control_plane_store(db_path=tmp_path / "auth.db")
        # Must never log a generated secret
        joined = "\n".join(r.message for r in caplog.records)
        assert "token_urlsafe" not in joined
        assert "password generated" not in joined.lower()
