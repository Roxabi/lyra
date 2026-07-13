"""Public session.resolve must receive session_token (not stripped by wrap)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard.auth_rpc import (
    handle_auth_login,
    handle_auth_session_resolve,
)
from factory.bootstrap.factory.dashboard.rpc_wrap import wrap_dashboard_handler
from factory.infrastructure.stores.identity.control_plane_store import ControlPlaneStore
from roxabi_contracts.dashboard import SUBJECTS


@pytest.mark.asyncio
async def test_wrap_session_resolve_keeps_token(tmp_path: Path) -> None:
    store = ControlPlaneStore(tmp_path / "auth.db")
    await store.connect()
    try:
        await store.bootstrap_admin_if_empty(
            email="a@test.local", password="admin-secret-99", display_name="A"
        )
        hub = MagicMock()
        hub._control_plane = store
        login = await handle_auth_login(
            hub, MagicMock(), {"email": "a@test.local", "password": "admin-secret-99"}
        )
        token = login["session_token"]
        cb = wrap_dashboard_handler(hub, MagicMock(), handle_auth_session_resolve)
        msg = MagicMock()
        msg.subject = SUBJECTS.auth_session_resolve
        msg.data = json.dumps({"session_token": token}).encode()
        msg.respond = AsyncMock()
        await cb(msg)
        raw = json.loads(msg.respond.await_args.args[0].decode())
        assert raw.get("ok") is True, raw
        assert raw["principal"]["user_id"] == login["principal"]["user_id"]
    finally:
        await store.close()
