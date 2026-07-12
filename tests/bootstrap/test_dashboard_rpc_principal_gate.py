"""Hub _wrap rejects missing principal (ADR-103 Block 5)."""

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
async def test_wrap_allows_with_principal() -> None:
    hub = MagicMock()
    nc = MagicMock()
    handler = AsyncMock(return_value={"ok": True})
    cb = _wrap(hub, nc, handler)
    principal = ControlPlanePrincipal(
        user_id="rx:user:admin",
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="session",
    )
    payload = stamp_principal_payload({}, principal)
    msg = MagicMock()
    msg.data = json.dumps(payload).encode()
    msg.subject = "factory.dashboard.jobs.list"
    msg.respond = AsyncMock()
    await cb(msg)
    handler.assert_awaited_once()
    # business payload must not carry principal_* keys
    assert handler.await_args is not None
    call_payload = handler.await_args.args[2]
    assert "principal_user_id" not in call_payload
    assert msg.respond.await_args is not None
    raw = json.loads(msg.respond.await_args.args[0].decode())
    assert raw["ok"] is True
