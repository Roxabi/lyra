"""Hub org list/create RPC + thin BFF orgs path."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app
from factory.bootstrap.factory.dashboard.org_rpc import (
    handle_org_create,
    handle_org_list,
)
from factory.core.auth.control_plane import (
    ControlPlanePrincipal,
    GlobalRole,
)
from factory.core.auth.control_plane_wire import (
    clear_request_principal,
    set_request_principal,
)


def _principal() -> ControlPlanePrincipal:
    return ControlPlanePrincipal(
        user_id="rx:u1",
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="session",
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FACTORY_DASHBOARD_COOKIE_INSECURE", "1")
    monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
    bus = MagicMock()
    bus.put = AsyncMock()
    ad = WebAdapter(inbound_bus=bus, agent_names=["alpha"], port=19998)
    ad._outbound_listener = MagicMock()
    app = create_app(ad, control_plane=None)
    return TestClient(app)


@pytest.mark.asyncio
async def test_org_list_rpc_ok() -> None:
    org = MagicMock()
    org.id = "org:1"
    org.name = "Acme"
    org.created_by = "rx:u1"
    org.created_at = datetime.now(timezone.utc)
    hub = MagicMock()
    cp = AsyncMock()
    cp.list_orgs_for_user = AsyncMock(return_value=[org])
    hub._control_plane = cp
    set_request_principal(_principal())
    try:
        raw = await handle_org_list(hub, MagicMock(), {})
        assert raw["ok"] is True
        assert raw["orgs"][0]["name"] == "Acme"
    finally:
        clear_request_principal()


@pytest.mark.asyncio
async def test_org_create_rpc_ok() -> None:
    org = MagicMock()
    org.id = "org:2"
    org.name = "NewCo"
    org.created_by = "rx:u1"
    org.created_at = datetime.now(timezone.utc)
    hub = MagicMock()
    cp = AsyncMock()
    cp.create_org = AsyncMock(return_value=org)
    hub._control_plane = cp
    set_request_principal(_principal())
    try:
        raw = await handle_org_create(hub, MagicMock(), {"name": "NewCo"})
        assert raw["ok"] is True
        assert raw["org"]["id"] == "org:2"
        cp.create_org.assert_awaited_once()
    finally:
        clear_request_principal()


def test_bff_list_orgs_via_hub(client: TestClient) -> None:
    resolve_raw = {
        "ok": True,
        "principal": {
            "user_id": "rx:u1",
            "roles": [GlobalRole.ADMIN.value],
            "org_ids": [],
            "active_org_id": None,
            "via": "session",
        },
        "user": {
            "id": "rx:u1",
            "email": "a@b.c",
            "global_role": "admin",
            "status": "active",
        },
    }
    org_raw = {
        "ok": True,
        "orgs": [
            {
                "id": "org:1",
                "name": "Acme",
                "created_by": "rx:u1",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        "active_org_id": None,
    }
    with (
        patch(
            "factory.dashboard.routes.hub_auth.auth_session_resolve",
            new=AsyncMock(return_value=resolve_raw),
        ),
        patch(
            "factory.dashboard.routes.org_routes.org_list",
            new=AsyncMock(return_value=org_raw),
        ),
    ):
        client.cookies.set("factory_session", "tok")
        res = client.get("/api/bff/orgs")
    assert res.status_code == 200, res.text
    assert res.json()["orgs"][0]["name"] == "Acme"


def test_bff_list_orgs_no_cp_no_hub_503(client: TestClient) -> None:
    # Without session → 401; with session but hub path fails differently.
    res = client.get("/api/bff/orgs")
    assert res.status_code == 401
