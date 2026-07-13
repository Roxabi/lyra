"""Hub password.change RPC handler."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard.auth_rpc import handle_auth_password_change
from factory.core.auth.control_plane import (
    ControlPlanePrincipal,
    ControlPlaneUser,
    GlobalRole,
    UserStatus,
)
from factory.core.auth.control_plane_wire import (
    clear_request_principal,
    set_request_principal,
)


@pytest.fixture
def user() -> ControlPlaneUser:
    return ControlPlaneUser(
        id="rx:u1",
        email="a@b.c",
        display_name="A",
        global_role=GlobalRole.ADMIN,
        status=UserStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        has_password=True,
    )


@pytest.mark.asyncio
async def test_password_change_ok(user: ControlPlaneUser) -> None:
    hub = MagicMock()
    cp = AsyncMock()
    cp.get_user = AsyncMock(return_value=user)
    cp.verify_password = AsyncMock(return_value=user)
    cp.set_password = AsyncMock()
    hub._control_plane = cp
    set_request_principal(
        ControlPlanePrincipal(
            user_id="rx:u1",
            roles=frozenset({"admin"}),
            org_ids=frozenset(),
            active_org_id=None,
            via="session",
        )
    )
    try:
        raw = await handle_auth_password_change(
            hub, MagicMock(), {"current_password": "old", "new_password": "newpass12"}
        )
        assert raw["ok"] is True
        cp.set_password.assert_awaited_once_with("rx:u1", "newpass12")
    finally:
        clear_request_principal()


@pytest.mark.asyncio
async def test_password_change_wrong_current(user: ControlPlaneUser) -> None:
    hub = MagicMock()
    cp = AsyncMock()
    cp.get_user = AsyncMock(return_value=user)
    cp.verify_password = AsyncMock(return_value=None)
    hub._control_plane = cp
    set_request_principal(
        ControlPlanePrincipal(
            user_id="rx:u1",
            roles=frozenset({"admin"}),
            org_ids=frozenset(),
            active_org_id=None,
            via="session",
        )
    )
    try:
        raw = await handle_auth_password_change(
            hub, MagicMock(), {"current_password": "bad", "new_password": "newpass12"}
        )
        assert raw["ok"] is False
        assert raw["error"] == "unauthorized"
    finally:
        clear_request_principal()
