"""Hub factory.dashboard.auth.* identity RPCs (ADR-103 Slice 1).

Calls real handlers against a real ControlPlaneStore (not a reimplementation).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard.auth_rpc import (
    handle_auth_invite_accept,
    handle_auth_invite_create,
    handle_auth_login,
    handle_auth_session_resolve,
)
from factory.bootstrap.factory.dashboard.rpc_wrap import wrap_dashboard_handler
from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole
from factory.core.auth.control_plane_wire import (
    clear_request_principal,
    set_request_principal,
)
from factory.infrastructure.stores.identity.control_plane_store import ControlPlaneStore
from roxabi_contracts.dashboard import SUBJECTS

_NC = MagicMock()


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


def _hub(cp: ControlPlaneStore) -> MagicMock:
    hub = MagicMock()
    hub._control_plane = cp
    return hub


@pytest.mark.asyncio
async def test_auth_login_happy(cp_store: ControlPlaneStore) -> None:
    hub = _hub(cp_store)
    out = await handle_auth_login(
        hub,
        _NC,
        {"email": "admin@test.local", "password": "admin-secret-99"},
    )
    assert out["ok"] is True
    assert out["session_token"]
    assert out["session_id"]
    assert out["user"]["email"] == "admin@test.local"
    assert GlobalRole.ADMIN.value in out["principal"]["roles"]
    assert out["principal"]["user_id"]
    assert out["principal"]["session_id"] == out["session_id"]


@pytest.mark.asyncio
async def test_auth_login_deny_bad_password(cp_store: ControlPlaneStore) -> None:
    hub = _hub(cp_store)
    out = await handle_auth_login(
        hub,
        _NC,
        {"email": "admin@test.local", "password": "wrong-password"},
    )
    assert out["ok"] is False
    assert out["error"] == "unauthorized"
    assert out.get("session_token") is None


@pytest.mark.asyncio
async def test_auth_session_resolve(cp_store: ControlPlaneStore) -> None:
    hub = _hub(cp_store)
    login = await handle_auth_login(
        hub,
        _NC,
        {"email": "admin@test.local", "password": "admin-secret-99"},
    )
    token = login["session_token"]
    out = await handle_auth_session_resolve(
        hub, _NC, {"session_token": token}
    )
    assert out["ok"] is True
    assert out["principal"]["user_id"] == login["principal"]["user_id"]
    assert GlobalRole.ADMIN.value in out["principal"]["roles"]


@pytest.mark.asyncio
async def test_auth_session_resolve_invalid(cp_store: ControlPlaneStore) -> None:
    hub = _hub(cp_store)
    out = await handle_auth_session_resolve(
        hub, _NC, {"session_token": "not-a-real-session-token"}
    )
    assert out["ok"] is False
    assert out["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_member_cannot_invite(cp_store: ControlPlaneStore) -> None:
    """Member principal must be denied invite.create (admin_only)."""
    hub = _hub(cp_store)
    # Create a member via invite accept
    admin = ControlPlanePrincipal(
        user_id=(await cp_store.get_user_by_email("admin@test.local")).id,  # type: ignore[union-attr]
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="session",
    )
    set_request_principal(admin)
    try:
        inv = await handle_auth_invite_create(
            hub, _NC, {"email": "member@test.local", "ttl_hours": 24}
        )
    finally:
        clear_request_principal()
    assert inv["ok"] is True
    accept = await handle_auth_invite_accept(
        hub,
        _NC,
        {
            "token": inv["token"],
            "password": "member-pass-99",
            "display_name": "Member",
        },
    )
    assert accept["ok"] is True
    member_uid = accept["principal"]["user_id"]

    set_request_principal(
        ControlPlanePrincipal(
            user_id=member_uid,
            roles=frozenset({GlobalRole.MEMBER.value}),
            org_ids=frozenset(),
            active_org_id=None,
            via="session",
        )
    )
    try:
        denied = await handle_auth_invite_create(
            hub, _NC, {"email": "other@test.local", "ttl_hours": 24}
        )
    finally:
        clear_request_principal()
    assert denied["ok"] is False
    assert denied["error"] == "forbidden"


@pytest.mark.asyncio
async def test_wrap_public_auth_login_without_principal(
    cp_store: ControlPlaneStore,
) -> None:
    """Login subject is public — wrap must not require principal stamp."""
    hub = _hub(cp_store)
    cb = wrap_dashboard_handler(hub, _NC, handle_auth_login)
    msg = MagicMock()
    msg.subject = SUBJECTS.auth_login
    msg.data = json.dumps(
        {"email": "admin@test.local", "password": "admin-secret-99"}
    ).encode()
    msg.respond = AsyncMock()
    await cb(msg)
    raw = json.loads(msg.respond.await_args.args[0].decode())
    assert raw["ok"] is True
    assert raw["session_token"]


@pytest.mark.asyncio
async def test_wrap_invite_create_requires_principal(
    cp_store: ControlPlaneStore,
) -> None:
    hub = _hub(cp_store)
    cb = wrap_dashboard_handler(hub, _NC, handle_auth_invite_create)
    msg = MagicMock()
    msg.subject = SUBJECTS.auth_invite_create
    msg.data = json.dumps({"email": "x@test.local"}).encode()
    msg.respond = AsyncMock()
    await cb(msg)
    raw = json.loads(msg.respond.await_args.args[0].decode())
    assert raw["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_subjects_under_auth_namespace() -> None:
    assert SUBJECTS.auth_login.startswith("factory.dashboard.auth.")
    assert SUBJECTS.auth_session_resolve.startswith("factory.dashboard.auth.")
    assert SUBJECTS.auth_invite_create.startswith("factory.dashboard.auth.")
    assert SUBJECTS.auth_invite_accept.startswith("factory.dashboard.auth.")
