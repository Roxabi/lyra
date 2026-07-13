"""Hub principal rehydrate — proof-bound, ignore client roles (ADR-103 Slice 3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard.auth_rpc import handle_auth_login
from factory.bootstrap.factory.dashboard.principal_rehydrate import rehydrate_principal
from factory.bootstrap.factory.dashboard.rpc_wrap import wrap_dashboard_handler
from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole
from factory.core.auth.control_plane_wire import stamp_principal_payload
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
    await store.create_user(
        email="member@test.local",
        password="member-secret-99",
        global_role=GlobalRole.MEMBER,
        display_name="Member",
    )
    try:
        yield store
    finally:
        await store.close()


def _hub(cp: ControlPlaneStore) -> MagicMock:
    hub = MagicMock()
    hub._control_plane = cp
    return hub


async def _login(hub: MagicMock, email: str, password: str) -> dict:
    return await handle_auth_login(
        hub, MagicMock(), {"email": email, "password": password}
    )


@pytest.mark.asyncio
async def test_rehydrate_no_proof_denies_even_with_user_id(
    cp_store: ControlPlaneStore,
) -> None:
    """Bare principal_user_id must not authenticate (impersonation closed)."""
    member = await cp_store.get_user_by_email("member@test.local")
    assert member is not None
    forged = ControlPlanePrincipal(
        user_id=member.id,
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="session",
    )
    hub = _hub(cp_store)
    principal = await rehydrate_principal(hub, wire=forged, payload={})
    assert principal is None


@pytest.mark.asyncio
async def test_rehydrate_invalid_session_denies(
    cp_store: ControlPlaneStore,
) -> None:
    hub = _hub(cp_store)
    principal = await rehydrate_principal(
        hub, wire=None, payload={"session_token": "not-a-real-token"}
    )
    assert principal is None


@pytest.mark.asyncio
async def test_rehydrate_via_session_token_ignores_forged_roles(
    cp_store: ControlPlaneStore,
) -> None:
    """Valid session proof + forged admin roles → store MEMBER roles only."""
    hub = _hub(cp_store)
    login = await _login(hub, "member@test.local", "member-secret-99")
    token = login["session_token"]
    member_id = login["principal"]["user_id"]
    forged = ControlPlanePrincipal(
        user_id=member_id,
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="session",
    )
    principal = await rehydrate_principal(
        hub, wire=forged, payload={"session_token": token}
    )
    assert principal is not None
    assert principal.user_id == member_id
    assert GlobalRole.MEMBER.value in principal.roles
    assert GlobalRole.ADMIN.value not in principal.roles


@pytest.mark.asyncio
async def test_rehydrate_via_api_key_proof(cp_store: ControlPlaneStore) -> None:
    hub = _hub(cp_store)
    login = await _login(hub, "admin@test.local", "admin-secret-99")
    user_id = login["principal"]["user_id"]
    _rec, secret = await cp_store.create_api_key(user_id, name="ci")
    principal = await rehydrate_principal(
        hub, wire=None, payload={"api_key": secret}
    )
    assert principal is not None
    assert GlobalRole.ADMIN.value in principal.roles


@pytest.mark.asyncio
async def test_wrap_business_rpc_requires_session_proof(
    cp_store: ControlPlaneStore,
) -> None:
    """Stamp alone (no session_token) → unauthorized on protected subject."""
    member = await cp_store.get_user_by_email("member@test.local")
    assert member is not None
    hub = _hub(cp_store)
    handler = AsyncMock(return_value={"ok": True})
    cb = wrap_dashboard_handler(hub, MagicMock(), handler)
    forged = ControlPlanePrincipal(
        user_id=member.id,
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="session",
    )
    payload = stamp_principal_payload({}, forged)
    # No session_token — proof missing
    assert "session_token" not in payload
    msg = MagicMock()
    msg.subject = "factory.dashboard.jobs.list"
    msg.data = json.dumps(payload).encode()
    msg.respond = AsyncMock()
    await cb(msg)
    handler.assert_not_awaited()
    raw = json.loads(msg.respond.await_args.args[0].decode())
    assert raw["error"] == "unauthorized"
    assert raw.get("ok") is False


@pytest.mark.asyncio
async def test_wrap_forged_roles_still_member_with_session_token(
    cp_store: ControlPlaneStore,
) -> None:
    """Protected subject + valid session + forged admin stamp → store MEMBER."""
    hub = _hub(cp_store)
    login = await _login(hub, "member@test.local", "member-secret-99")
    token = login["session_token"]
    member_id = login["principal"]["user_id"]
    seen: list[ControlPlanePrincipal | None] = []

    async def _handler(_hub, _nc, _payload):
        from factory.core.auth.control_plane_wire import get_request_principal

        seen.append(get_request_principal())
        return {"ok": True}

    cb = wrap_dashboard_handler(hub, MagicMock(), _handler)
    forged = ControlPlanePrincipal(
        user_id=member_id,
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="session",
    )
    payload = stamp_principal_payload({"session_token": token}, forged)
    msg = MagicMock()
    msg.subject = "factory.dashboard.jobs.list"
    msg.data = json.dumps(payload).encode()
    msg.respond = AsyncMock()
    await cb(msg)
    assert seen and seen[0] is not None
    assert GlobalRole.MEMBER.value in seen[0].roles
    assert GlobalRole.ADMIN.value not in seen[0].roles
    raw = json.loads(msg.respond.await_args.args[0].decode())
    assert raw.get("ok") is True
