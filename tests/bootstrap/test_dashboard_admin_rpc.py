"""Unit tests for dashboard admin NATS RPC handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard.admin_rpc import (
    handle_admin_access,
    handle_admin_user_create,
    handle_admin_user_patch,
)
from factory.core.agent.agent_models import AgentRow
from factory.core.auth.agent_grants import AuthDecision
from factory.infrastructure.stores.identity.user_store import UserStore

_NC = MagicMock()


def _hub_with_stores(
    *,
    user_store: UserStore,
    grant_store: MagicMock,
    agent_rows: list[AgentRow] | None = None,
) -> MagicMock:
    hub = MagicMock()
    hub._user_store = user_store
    hub._authorizer = grant_store
    agent_store = MagicMock()
    agent_store.get_all.return_value = agent_rows or []
    agent_store.get_all_bot_mappings.return_value = {}
    hub._agent_store = agent_store
    return hub


def _grant_store(*, allowed_agents: set[str] | None = None) -> MagicMock:
    allowed = set(allowed_agents or [])

    def _authorize(*, agent_name: str, user_id: str, roles=()) -> AuthDecision:
        if agent_name in allowed:
            return AuthDecision.allow()
        return AuthDecision.deny()

    async def _grant(agent_name: str, _principal, **_kwargs) -> None:
        allowed.add(agent_name)

    async def _revoke(agent_name: str, _principal, **_kwargs) -> None:
        allowed.discard(agent_name)

    store = MagicMock()
    store.authorize.side_effect = _authorize
    store.grant = AsyncMock(side_effect=_grant)
    store.revoke = AsyncMock(side_effect=_revoke)
    return store


@pytest.mark.asyncio
async def test_handle_admin_access_lists_users_with_agents(tmp_path) -> None:
    user_store = UserStore(db_path=tmp_path / "auth.db")
    await user_store.connect()
    try:
        user = await user_store.create_profile_user(
            display_name="Jane",
            email="jane@example.com",
        )
        grant_store = _grant_store(allowed_agents={"lyra"})
        hub = _hub_with_stores(
            user_store=user_store,
            grant_store=grant_store,
            agent_rows=[AgentRow(name="lyra", backend="claude-cli", model="sonnet")],
        )

        out = await handle_admin_access(hub, _NC, {})

        assert len(out["users"]) == 1
        assert out["users"][0]["user_id"] == user.id
        assert out["users"][0]["agents"] == ["lyra"]
    finally:
        await user_store.close()


@pytest.mark.asyncio
async def test_handle_admin_user_create_grants_agents(tmp_path) -> None:
    user_store = UserStore(db_path=tmp_path / "auth.db")
    await user_store.connect()
    try:
        grant_store = _grant_store()
        hub = _hub_with_stores(
            user_store=user_store,
            grant_store=grant_store,
            agent_rows=[AgentRow(name="lyra", backend="claude-cli", model="sonnet")],
        )

        out = await handle_admin_user_create(
            hub,
            _NC,
            {
                "body": {
                    "display_name": "Ops",
                    "email": "ops@example.com",
                    "agents": ["lyra"],
                }
            },
        )

        assert "error" not in out
        assert out["agents"] == ["lyra"]
        grant_store.grant.assert_awaited_once()
    finally:
        await user_store.close()


@pytest.mark.asyncio
async def test_admin_user_patch_preserves_telegram_when_omitted(tmp_path) -> None:
    user_store = UserStore(db_path=tmp_path / "auth.db")
    await user_store.connect()
    try:
        user = await user_store.create_profile_user(
            display_name="Jane",
            email="jane@example.com",
        )
        await user_store.set_platform_identity(user.id, "telegram", "12345")
        grant_store = _grant_store()
        hub = _hub_with_stores(
            user_store=user_store,
            grant_store=grant_store,
            agent_rows=[],
        )

        out = await handle_admin_user_patch(
            hub,
            _NC,
            {
                "user_id": user.id,
                "patch": {"display_name": "Jane Updated"},
            },
        )

        assert "error" not in out
        identities = await user_store.list_platform_identities(user.id)
        telegram = next(i for i in identities if i.platform == "telegram")
        assert telegram.platform_uid == "12345"
        assert out["display_name"] == "Jane Updated"
    finally:
        await user_store.close()


@pytest.mark.asyncio
async def test_handle_admin_user_patch_clears_telegram_with_null(tmp_path) -> None:
    user_store = UserStore(db_path=tmp_path / "auth.db")
    await user_store.connect()
    try:
        user = await user_store.create_profile_user(
            display_name="Jane",
            email="jane@example.com",
        )
        await user_store.set_platform_identity(user.id, "telegram", "12345")
        grant_store = _grant_store()
        hub = _hub_with_stores(
            user_store=user_store,
            grant_store=grant_store,
            agent_rows=[],
        )

        out = await handle_admin_user_patch(
            hub,
            _NC,
            {
                "user_id": user.id,
                "patch": {"telegram_uid": None},
            },
        )

        assert "error" not in out
        assert out["telegram"] is None
    finally:
        await user_store.close()


@pytest.mark.asyncio
async def test_handle_admin_user_create_rejects_unknown_agent(tmp_path) -> None:
    user_store = UserStore(db_path=tmp_path / "auth.db")
    await user_store.connect()
    try:
        grant_store = _grant_store()
        hub = _hub_with_stores(
            user_store=user_store,
            grant_store=grant_store,
            agent_rows=[],
        )

        out = await handle_admin_user_create(
            hub,
            _NC,
            {
                "body": {
                    "display_name": "Ops",
                    "email": "ops@example.com",
                    "agents": ["missing"],
                }
            },
        )

        assert out["error"] == "conflict"
        assert "unknown agent" in out.get("message", "")
    finally:
        await user_store.close()