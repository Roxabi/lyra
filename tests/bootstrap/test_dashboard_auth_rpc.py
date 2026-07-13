"""Hub factory.dashboard.auth.* identity RPCs (ADR-103 Slice 1).

Calls real handlers against a real ControlPlaneStore (not a reimplementation).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard.auth_rpc import (
    handle_auth_api_key_resolve,
    handle_auth_invite_accept,
    handle_auth_invite_create,
    handle_auth_login,
    handle_auth_logout,
    handle_auth_session_resolve,
)
from factory.bootstrap.factory.dashboard.rpc_wrap import (
    _public_auth_subjects,
    wrap_dashboard_handler,
)
from factory.core.auth.control_plane import GlobalRole
from factory.core.auth.control_plane_wire import stamp_principal_payload
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


async def _admin_login(hub: MagicMock) -> dict:
    return await handle_auth_login(
        hub,
        _NC,
        {"email": "admin@test.local", "password": "admin-secret-99"},
    )


@pytest.mark.asyncio
async def test_auth_login_happy(cp_store: ControlPlaneStore) -> None:
    hub = _hub(cp_store)
    out = await _admin_login(hub)
    assert out["ok"] is True
    assert out["session_token"]
    assert out["session_id"]
    assert out["user"]["email"] == "admin@test.local"
    assert GlobalRole.ADMIN.value in out["principal"]["roles"]
    assert out["principal"]["user_id"]
    assert out["principal"]["session_id"] == out["session_id"]
    assert out["ttl_seconds"] and out["ttl_seconds"] > 0


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
    login = await _admin_login(hub)
    token = login["session_token"]
    out = await handle_auth_session_resolve(hub, _NC, {"session_token": token})
    assert out["ok"] is True
    assert out["session_id"] == login["session_id"]
    assert out["principal"]["session_id"] == login["session_id"]
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
async def test_auth_logout_revokes_session(cp_store: ControlPlaneStore) -> None:
    hub = _hub(cp_store)
    login = await _admin_login(hub)
    token = login["session_token"]
    assert (await handle_auth_session_resolve(hub, _NC, {"session_token": token}))[
        "ok"
    ]
    out = await handle_auth_logout(hub, _NC, {"session_token": token})
    assert out["ok"] is True
    denied = await handle_auth_session_resolve(hub, _NC, {"session_token": token})
    assert denied["ok"] is False


@pytest.mark.asyncio
async def test_auth_api_key_resolve(cp_store: ControlPlaneStore) -> None:
    hub = _hub(cp_store)
    login = await _admin_login(hub)
    user_id = login["principal"]["user_id"]
    rec, secret = await cp_store.create_api_key(user_id, name="ci")
    out = await handle_auth_api_key_resolve(hub, _NC, {"api_key": secret})
    assert out["ok"] is True
    assert out["api_key_id"] == rec.id
    assert out["principal"]["api_key_id"] == rec.id
    assert GlobalRole.ADMIN.value in out["principal"]["roles"]
    bad = await handle_auth_api_key_resolve(hub, _NC, {"api_key": "fak_notreal"})
    assert bad["ok"] is False


@pytest.mark.asyncio
async def test_member_cannot_invite(cp_store: ControlPlaneStore) -> None:
    """Member session rehydrate must deny invite.create (admin_only)."""
    hub = _hub(cp_store)
    admin_login = await _admin_login(hub)
    inv = await handle_auth_invite_create(
        hub,
        _NC,
        {
            "email": "member@test.local",
            "ttl_hours": 24,
            "session_token": admin_login["session_token"],
        },
    )
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
    assert GlobalRole.MEMBER.value in accept["principal"]["roles"]
    assert GlobalRole.ADMIN.value not in accept["principal"]["roles"]
    assert accept["session_token"]

    denied = await handle_auth_invite_create(
        hub,
        _NC,
        {
            "email": "other@test.local",
            "ttl_hours": 24,
            "session_token": accept["session_token"],
        },
    )
    assert denied["ok"] is False
    assert denied["error"] == "forbidden"


@pytest.mark.asyncio
async def test_forged_admin_wire_roles_cannot_invite(
    cp_store: ControlPlaneStore,
) -> None:
    """Wire-stamped admin roles without valid session_token must not mint invites."""
    hub = _hub(cp_store)
    # No session_token — even with forged principal stamp on wrap path
    out = await handle_auth_invite_create(
        hub, _NC, {"email": "evil@test.local", "ttl_hours": 24}
    )
    assert out["ok"] is False
    assert out["error"] == "unauthorized"

    forged = stamp_principal_payload(
        {"email": "evil@test.local", "ttl_hours": 24},
        __import__(
            "factory.core.auth.control_plane", fromlist=["ControlPlanePrincipal"]
        ).ControlPlanePrincipal(
            user_id="rx:forged",
            roles=frozenset({GlobalRole.ADMIN.value}),
            org_ids=frozenset(),
            active_org_id=None,
            via="session",
        ),
    )
    # Handler ignores wire roles; still requires session_token
    out2 = await handle_auth_invite_create(hub, _NC, forged)
    assert out2["ok"] is False
    assert out2["error"] == "unauthorized"


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
    msg.data = json.dumps(
        {"email": "x@test.local", "session_token": "nope"}
    ).encode()
    msg.respond = AsyncMock()
    await cb(msg)
    raw = json.loads(msg.respond.await_args.args[0].decode())
    assert raw.get("ok") is False
    assert raw["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_subjects_under_auth_namespace() -> None:
    expected = {
        SUBJECTS.auth_login,
        SUBJECTS.auth_logout,
        SUBJECTS.auth_session_resolve,
        SUBJECTS.auth_invite_create,
        SUBJECTS.auth_invite_accept,
        SUBJECTS.auth_api_key_resolve,
    }
    assert expected == {
        "factory.dashboard.auth.login",
        "factory.dashboard.auth.logout",
        "factory.dashboard.auth.session.resolve",
        "factory.dashboard.auth.invite.create",
        "factory.dashboard.auth.invite.accept",
        "factory.dashboard.auth.api_key.resolve",
    }
    public = _public_auth_subjects()
    assert SUBJECTS.auth_invite_create not in public
    assert public == {
        SUBJECTS.auth_login,
        SUBJECTS.auth_logout,
        SUBJECTS.auth_session_resolve,
        SUBJECTS.auth_invite_accept,
        SUBJECTS.auth_api_key_resolve,
    }
