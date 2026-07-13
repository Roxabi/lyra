"""BFF API-key principal must stamp api_key proof for hub rehydrate (Slice 3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app
from factory.bootstrap.factory.dashboard.rpc_wrap import wrap_dashboard_handler
from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole
from factory.core.auth.control_plane_wire import (
    clear_request_principal,
    get_request_api_key,
    stamp_principal_payload,
)
from factory.dashboard.auth import require_principal
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


@pytest.mark.asyncio
async def test_require_principal_api_key_stamps_proof_context(
    cp_store: ControlPlaneStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After Bearer API-key auth, stamp context holds api_key for hub wire."""
    monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
    user = await cp_store.get_user_by_email("admin@test.local")
    assert user is not None
    _rec, secret = await cp_store.create_api_key(user.id, name="ci")

    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(inbound_bus=bus, agent_names=["alpha"], port=19995)
    adapter._outbound_listener = MagicMock()
    app = create_app(adapter, control_plane=cp_store)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/bff/auth/me",
        "headers": [
            (b"authorization", f"Bearer {secret}".encode()),
        ],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "scheme": "http",
        "app": app,
    }
    request = Request(scope)
    try:
        principal = await require_principal(
            request, authorization=f"Bearer {secret}", factory_session=None
        )
        assert principal.via == "api_key"
        assert get_request_api_key() == secret
        stamped = stamp_principal_payload({}, principal)
        assert stamped.get("api_key") == secret
    finally:
        clear_request_principal()


@pytest.mark.asyncio
async def test_api_key_stamp_rehydrates_on_hub_business_rpc(
    cp_store: ControlPlaneStore,
) -> None:
    """End path: stamp with api_key proof → wrap rehydrate → handler runs."""
    user = await cp_store.get_user_by_email("admin@test.local")
    assert user is not None
    _rec, secret = await cp_store.create_api_key(user.id, name="ci")

    hub = MagicMock()
    hub._control_plane = cp_store
    seen: list = []

    async def _handler(_hub, _nc, _payload):
        from factory.core.auth.control_plane_wire import get_request_principal

        seen.append(get_request_principal())
        return {"ok": True}

    cb = wrap_dashboard_handler(hub, MagicMock(), _handler)
    forged = ControlPlanePrincipal(
        user_id=user.id,
        roles=frozenset({GlobalRole.MEMBER.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="api_key",
    )
    payload = stamp_principal_payload({"api_key": secret}, forged)
    msg = MagicMock()
    msg.subject = "factory.dashboard.jobs.list"
    msg.data = json.dumps(payload).encode()
    msg.respond = AsyncMock()
    await cb(msg)
    assert seen and seen[0] is not None
    assert GlobalRole.ADMIN.value in seen[0].roles
    raw = json.loads(msg.respond.await_args.args[0].decode())
    assert raw.get("ok") is True


@pytest.mark.asyncio
async def test_api_key_stamp_without_proof_fails_hub_rpc(
    cp_store: ControlPlaneStore,
) -> None:
    user = await cp_store.get_user_by_email("admin@test.local")
    assert user is not None
    hub = MagicMock()
    hub._control_plane = cp_store
    handler = AsyncMock(return_value={"ok": True})
    cb = wrap_dashboard_handler(hub, MagicMock(), handler)
    principal = ControlPlanePrincipal(
        user_id=user.id,
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="api_key",
    )
    payload = stamp_principal_payload({}, principal)
    assert "api_key" not in payload
    msg = MagicMock()
    msg.subject = "factory.dashboard.jobs.list"
    msg.data = json.dumps(payload).encode()
    msg.respond = AsyncMock()
    await cb(msg)
    handler.assert_not_awaited()
    raw = json.loads(msg.respond.await_args.args[0].decode())
    assert raw["error"] == "unauthorized"


def test_bff_bearer_api_key_me_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP path: Bearer API key resolves require_principal (local CP)."""
    import asyncio

    monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
    monkeypatch.setenv("FACTORY_DASHBOARD_COOKIE_INSECURE", "1")

    async def _setup() -> tuple[ControlPlaneStore, str]:
        store = ControlPlaneStore(tmp_path / "auth.db")
        await store.connect()
        await store.bootstrap_admin_if_empty(
            email="admin@test.local",
            password="admin-secret-99",
            display_name="Admin",
        )
        user = await store.get_user_by_email("admin@test.local")
        assert user is not None
        _r, secret = await store.create_api_key(user.id, name="http")
        return store, secret

    store, secret = asyncio.run(_setup())
    try:
        bus = MagicMock()
        bus.put = AsyncMock()
        adapter = WebAdapter(inbound_bus=bus, agent_names=["alpha"], port=19994)
        adapter._outbound_listener = MagicMock()
        app = create_app(adapter, control_plane=store)
        client = TestClient(app)
        me = client.get(
            "/api/bff/auth/me", headers={"Authorization": f"Bearer {secret}"}
        )
        assert me.status_code == 200, me.text
        assert me.json()["principal"]["via"] == "api_key"
    finally:
        asyncio.run(store.close())
