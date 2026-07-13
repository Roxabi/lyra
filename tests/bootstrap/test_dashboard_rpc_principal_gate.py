"""Hub _wrap rejects missing principal / missing proof (ADR-103 Block 5 + Slice 3)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard.rpc_wrap import wrap_dashboard_handler as _wrap
from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole
from factory.core.auth.control_plane_wire import stamp_principal_payload


@pytest.mark.asyncio
async def test_wrap_denies_without_principal() -> None:
    hub = MagicMock()
    nc = MagicMock()
    handler = AsyncMock(return_value={"ok": True})
    cb = _wrap(hub, nc, handler)
    msg = MagicMock()
    msg.data = b"{}"
    msg.subject = "factory.dashboard.jobs.list"
    msg.respond = AsyncMock()
    await cb(msg)
    handler.assert_not_awaited()
    raw = json.loads(msg.respond.await_args.args[0].decode())
    assert raw["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_wrap_denies_user_id_stamp_without_session_token(tmp_path) -> None:
    """Forged principal_user_id alone must not unlock business RPCs."""
    from factory.infrastructure.stores.identity.control_plane_store import (
        ControlPlaneStore,
    )

    store = ControlPlaneStore(tmp_path / "auth.db")
    await store.connect()
    try:
        await store.bootstrap_admin_if_empty(
            email="admin@test.local",
            password="admin-secret-99",
            display_name="Admin",
        )
        user = await store.get_user_by_email("admin@test.local")
        assert user is not None
        hub = MagicMock()
        hub._control_plane = store
        handler = AsyncMock(return_value={"ok": True})
        cb = _wrap(hub, MagicMock(), handler)
        principal = ControlPlanePrincipal(
            user_id=user.id,
            roles=frozenset({GlobalRole.ADMIN.value}),
            org_ids=frozenset(),
            active_org_id=None,
            via="session",
        )
        payload = stamp_principal_payload({}, principal)
        assert "session_token" not in payload
        msg = MagicMock()
        msg.data = json.dumps(payload).encode()
        msg.subject = "factory.dashboard.jobs.list"
        msg.respond = AsyncMock()
        await cb(msg)
        handler.assert_not_awaited()
        raw = json.loads(msg.respond.await_args.args[0].decode())
        assert raw["error"] == "unauthorized"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_wrap_allows_with_session_proof(tmp_path) -> None:
    """Happy path: real session_token + optional stamp → handler runs."""
    from factory.bootstrap.factory.dashboard.auth_rpc import handle_auth_login
    from factory.infrastructure.stores.identity.control_plane_store import (
        ControlPlaneStore,
    )

    store = ControlPlaneStore(tmp_path / "auth.db")
    await store.connect()
    try:
        await store.bootstrap_admin_if_empty(
            email="admin@test.local",
            password="admin-secret-99",
            display_name="Admin",
        )
        hub = MagicMock()
        hub._control_plane = store
        login = await handle_auth_login(
            hub,
            MagicMock(),
            {"email": "admin@test.local", "password": "admin-secret-99"},
        )
        token = login["session_token"]
        user_id = login["principal"]["user_id"]
        handler = AsyncMock(return_value={"ok": True})
        cb = _wrap(hub, MagicMock(), handler)
        # Wire roles forged to member — rehydrate must still use store admin
        principal = ControlPlanePrincipal(
            user_id=user_id,
            roles=frozenset({GlobalRole.MEMBER.value}),
            org_ids=frozenset(),
            active_org_id=None,
            via="session",
        )
        payload = stamp_principal_payload({"session_token": token}, principal)
        msg = MagicMock()
        msg.data = json.dumps(payload).encode()
        msg.subject = "factory.dashboard.jobs.list"
        msg.respond = AsyncMock()
        await cb(msg)
        handler.assert_awaited_once()
        assert handler.await_args is not None
        call_payload = handler.await_args.args[2]
        assert "principal_user_id" not in call_payload
        assert "session_token" not in call_payload
        raw = json.loads(msg.respond.await_args.args[0].decode())
        assert raw["ok"] is True
    finally:
        await store.close()
