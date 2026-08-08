"""Thin BFF auth via hub RPC — no auth.db open in web adapter (ADR-103 Slice 2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app
from factory.core.auth.control_plane import GlobalRole
from factory.dashboard.routes.hub_auth import HubAuthError


@pytest.fixture
def adapter() -> WebAdapter:
    bus = MagicMock()
    bus.put = AsyncMock()
    ad = WebAdapter(inbound_bus=bus, agent_names=["alpha"], port=19997)
    ad._outbound_listener = MagicMock()
    return ad


@pytest.fixture
def client(adapter: WebAdapter, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FACTORY_DASHBOARD_COOKIE_INSECURE", "1")
    monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
    # Production path: no local control_plane
    app = create_app(adapter, control_plane=None)
    return TestClient(app)


def test_web_adapter_astart_does_not_import_open_control_plane() -> None:
    """Static gate: dual-open path removed from web adapter composition root."""
    import inspect

    from factory.adapters.web import web_adapter as wa

    src = inspect.getsource(wa.WebAdapter.astart)
    assert "open_control_plane_store" not in src
    assert "await open_control_plane" not in src
    assert "control_plane=None" in src


def test_login_via_hub_sets_cookie(client: TestClient) -> None:
    hub_raw = {
        "ok": True,
        "session_token": "opaque-session-token-xyz",
        "session_id": "ses:abc",
        "ttl_seconds": 1200,
        "user": {
            "id": "rx:u1",
            "email": "admin@test.local",
            "display_name": "Admin",
            "global_role": "admin",
            "status": "active",
        },
        "principal": {
            "user_id": "rx:u1",
            "roles": [GlobalRole.ADMIN.value],
            "org_ids": [],
            "active_org_id": None,
            "via": "session",
            "session_id": "ses:abc",
        },
    }
    with patch(
        "factory.dashboard.routes.auth_routes.auth_login",
        new=AsyncMock(return_value=hub_raw),
    ):
        res = client.post(
            "/api/bff/auth/login",
            json={"email": "admin@test.local", "password": "secret"},
        )
    assert res.status_code == 200, res.text
    assert res.json()["user"]["email"] == "admin@test.local"
    assert "factory_session" in res.cookies
    assert res.cookies["factory_session"] == "opaque-session-token-xyz"


def test_login_deny_via_hub(client: TestClient) -> None:
    with patch(
        "factory.dashboard.routes.auth_routes.auth_login",
        new=AsyncMock(
            side_effect=HubAuthError("unauthorized", "invalid email or password")
        ),
    ):
        res = client.post(
            "/api/bff/auth/login",
            json={"email": "admin@test.local", "password": "wrong"},
        )
    assert res.status_code == 401


def test_hub_unauthorized_surfaces_as_http_401(client: TestClient) -> None:
    """Prod path: hub_client raises HubUnauthorizedError on login_deny (#auth 500).

    Name avoids ``test_<long_snake>`` patterns that TruffleHog Lob detector
    false-positives as live test API keys (verified against Lob's API).
    """
    from factory.dashboard.hub_client import HubUnauthorizedError

    mock_hub = MagicMock()
    mock_hub._request = AsyncMock(
        side_effect=HubUnauthorizedError("invalid email or password")
    )

    def _hub_from_app(request):  # noqa: ANN001
        del request
        return mock_hub

    with patch(
        "factory.dashboard.routes.auth_routes.hub_client_from_app",
        side_effect=_hub_from_app,
    ):
        res = client.post(
            "/api/bff/auth/login",
            json={"email": "admin@test.local", "password": "wrong"},
        )
    assert res.status_code == 401, res.text
    assert res.json()["detail"] == "invalid email or password"
    assert "factory_session" not in res.cookies


def test_hub_forbidden_surfaces_as_http_403(client: TestClient) -> None:
    """_rpc maps HubForbiddenError → HubAuthError(forbidden) → HTTP 403."""
    from factory.dashboard.hub_client import HubForbiddenError

    mock_hub = MagicMock()
    mock_hub._request = AsyncMock(
        side_effect=HubForbiddenError("not allowed for this principal")
    )

    def _hub_from_app(request):  # noqa: ANN001
        del request
        return mock_hub

    with patch(
        "factory.dashboard.routes.auth_routes.hub_client_from_app",
        side_effect=_hub_from_app,
    ):
        res = client.post(
            "/api/bff/auth/login",
            json={"email": "admin@test.local", "password": "secret"},
        )
    assert res.status_code == 403, res.text
    assert res.json()["detail"] == "not allowed for this principal"
    assert "factory_session" not in res.cookies


def test_me_via_hub_session_resolve(client: TestClient) -> None:
    resolve_raw = {
        "ok": True,
        "session_id": "ses:abc",
        "principal": {
            "user_id": "rx:u1",
            "roles": [GlobalRole.ADMIN.value],
            "org_ids": [],
            "active_org_id": None,
            "via": "session",
            "session_id": "ses:abc",
        },
        "user": {
            "id": "rx:u1",
            "email": "admin@test.local",
            "global_role": "admin",
            "status": "active",
        },
    }
    # require_principal imports hub_auth at call time; /me uses auth_routes binding.
    with (
        patch(
            "factory.dashboard.routes.hub_auth.auth_session_resolve",
            new=AsyncMock(return_value=resolve_raw),
        ),
        patch(
            "factory.dashboard.routes.auth_routes.auth_session_resolve",
            new=AsyncMock(return_value=resolve_raw),
        ),
    ):
        client.cookies.set("factory_session", "opaque-session-token-xyz")
        me = client.get("/api/bff/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["principal"]["user_id"] == "rx:u1"
    assert me.json()["principal"]["via"] == "session"
    assert me.json()["user"]["email"] == "admin@test.local"


def test_me_unauth_401(client: TestClient) -> None:
    res = client.get("/api/bff/auth/me")
    assert res.status_code == 401


def test_logout_via_hub(client: TestClient) -> None:
    resolve_raw = {
        "ok": True,
        "principal": {
            "user_id": "rx:u1",
            "roles": ["admin"],
            "org_ids": [],
            "active_org_id": None,
            "via": "session",
        },
    }
    with (
        patch(
            "factory.dashboard.routes.hub_auth.auth_session_resolve",
            new=AsyncMock(return_value=resolve_raw),
        ),
        patch(
            "factory.dashboard.routes.auth_routes.auth_logout",
            new=AsyncMock(return_value={"ok": True}),
        ) as logout_mock,
    ):
        client.cookies.set("factory_session", "tok")
        res = client.post("/api/bff/auth/logout")
    assert res.status_code == 200
    logout_mock.assert_awaited()
