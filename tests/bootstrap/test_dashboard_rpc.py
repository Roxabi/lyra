"""Unit tests for hub dashboard NATS RPC handlers (#1771)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory import dashboard_rpc
from factory.bootstrap.factory.dashboard_agents_rpc import handle_agents_list
from factory.bootstrap.factory.dashboard_jobs_rpc import handle_jobs_list
from factory.bootstrap.factory.dashboard_rpc import (
    _handle_agents_status,
    _handle_connectors_delete,
    _handle_connectors_list,
    _handle_connectors_upsert,
    _handle_sessions_list,
    _handle_sessions_resume,
    _handle_sessions_turns,
)
from factory.core.agent.agent_models import AgentRow
from factory.core.hub.hub_protocol import Binding, RoutingKey
from factory.core.messaging.message import Platform
from factory.dashboard.heartbeat import queue_group_alive
from factory.infrastructure.stores.ingress.installation_store import InstallationStore

_NC = MagicMock()


@pytest.mark.asyncio
async def test_sessions_list_empty_without_turn_store() -> None:
    hub = MagicMock()
    hub._turn_store = None
    hub.bindings = {}
    out = await _handle_sessions_list(hub, _NC, {"agent": "lyra", "limit": 10})
    assert out["sessions"] == []


@pytest.mark.asyncio
async def test_sessions_list_delegates_to_catalog() -> None:
    hub = MagicMock()
    store = MagicMock()
    hub._turn_store = store
    hub.bindings = {
        RoutingKey(Platform.WEB, "smoke", "agent:lyra"): Binding(
            agent_name="lyra", pool_id="web:smoke:agent:lyra"
        ),
    }
    store.list_recent_sessions = AsyncMock(
        return_value=[
            {
                "session_id": "s1",
                "pool_id": "web:smoke:agent:lyra",
                "platform": "web",
                "cli_session_id": "cli-1",
                "first_user_msg": "hi",
                "turn_count": 2,
                "last_active_at": "2026-06-28T00:00:00Z",
            }
        ]
    )
    out = await _handle_sessions_list(hub, _NC, {"agent": "lyra", "limit": 5})
    assert len(out["sessions"]) == 1
    assert out["sessions"][0]["platform"] == "web"
    store.list_recent_sessions.assert_awaited_once()


@pytest.mark.asyncio
async def test_agents_status_marks_offline_without_heartbeat() -> None:
    hub = MagicMock()
    hub.agent_registry = ["lyra"]
    hub._dashboard_worker_freshness = {}
    out = await _handle_agents_status(hub, _NC, {"agents": ["lyra"]})
    assert out["agents"][0]["online"] is False


@pytest.mark.asyncio
async def test_agents_status_online_with_dynamic_clipool_worker_id() -> None:
    import time

    hub = MagicMock()
    hub.agent_registry = ["lyra"]
    hub._dashboard_worker_freshness = {
        "clipool-workers-factory-clipool-12345": time.monotonic(),
    }
    out = await _handle_agents_status(hub, _NC, {"agents": ["lyra"]})
    assert out["agents"][0]["harness_reachable"] is True
    assert out["agents"][0]["online"] is True


@pytest.mark.asyncio
async def test_agents_status_respects_harness_selection() -> None:
    import time

    hub = MagicMock()
    hub.agent_registry = ["lyra"]
    hub._dashboard_worker_freshness = {
        "clipool-workers-factory-clipool-12345": time.monotonic(),
        "omp-workers-factory-omp-99": time.monotonic(),
    }
    out = await _handle_agents_status(
        hub,
        _NC,
        {"agents": ["lyra"], "harness_by_agent": {"lyra": "omp-rpc"}},
    )
    assert out["agents"][0]["harness"] == "omp-rpc"
    assert out["agents"][0]["harness_reachable"] is True
    assert out["agents"][0]["online"] is True


def test_queue_group_alive_matches_dynamic_worker_id_prefix() -> None:
    import time

    freshness = {"clipool-workers-roxabituwer-42": time.monotonic()}
    assert queue_group_alive(freshness, "clipool-workers") is True
    assert queue_group_alive(freshness, "omp-workers") is False


def test_queue_group_alive_rejects_stale_heartbeat() -> None:
    import time

    freshness = {
        "clipool-workers-roxabituwer-42": time.monotonic() - 60.0,
    }
    assert queue_group_alive(freshness, "clipool-workers") is False


@pytest.mark.asyncio
async def test_agents_list_rpc_handler_returns_summaries() -> None:
    hub = MagicMock()
    store = MagicMock()
    store.get_all.return_value = [
        AgentRow(name="lyra", backend="claude-cli", model="sonnet"),
    ]
    hub._agent_store = store
    out = await handle_agents_list(hub, _NC, {})
    assert out["agents"][0]["name"] == "lyra"
    assert out["agents"][0]["backend"] == "claude-cli"


@pytest.mark.asyncio
async def test_jobs_list_empty_without_registry() -> None:
    hub = MagicMock()
    hub.bindings = {}
    hub._active_jobs_coord = None
    hub._active_jobs_store = None
    out = await handle_jobs_list(hub, _NC, {})
    assert out["jobs"] == []


@pytest.mark.asyncio
async def test_sessions_turns_empty_without_turn_store() -> None:
    hub = MagicMock()
    hub._turn_store = None
    out = await _handle_sessions_turns(hub, _NC, {"session_id": "s1", "limit": 50})
    assert out["turns"] == []


@pytest.mark.asyncio
async def test_sessions_turns_returns_user_assistant_rows() -> None:
    hub = MagicMock()
    store = MagicMock()
    hub._turn_store = store
    store.get_turns_by_session = AsyncMock(
        return_value=[
            {
                "role": "user",
                "content": "hi",
                "timestamp": "2026-06-28T00:00:00Z",
                "pool_id": "p",
                "session_id": "s1",
                "platform": "web",
                "user_id": "u",
                "id": 1,
                "message_id": None,
                "reply_message_id": None,
                "metadata": {},
            },
            {
                "role": "assistant",
                "content": "hello",
                "timestamp": "2026-06-28T00:00:01Z",
                "pool_id": "p",
                "session_id": "s1",
                "platform": "web",
                "user_id": "u",
                "id": 2,
                "message_id": None,
                "reply_message_id": None,
                "metadata": {},
            },
        ]
    )
    out = await _handle_sessions_turns(hub, _NC, {"session_id": "s1", "limit": 50})
    assert len(out["turns"]) == 2
    assert out["turns"][0]["content"] == "hi"


@pytest.fixture
def installation_store(tmp_path):
    store = InstallationStore(tmp_path / "ingress.db")
    asyncio.run(store.connect())
    yield store
    asyncio.run(store.close())


@pytest.fixture
def patch_installation_store(installation_store, monkeypatch: pytest.MonkeyPatch):
    async def _get_store():
        return installation_store

    monkeypatch.setattr(dashboard_rpc, "get_installation_store", _get_store)


@pytest.mark.asyncio
async def test_connectors_list_filters_by_tenant(
    patch_installation_store, installation_store
) -> None:
    await installation_store.upsert_lifecycle("github", "111", "default", enabled=True)
    await installation_store.upsert_lifecycle("github", "222", "other", enabled=True)
    hub = MagicMock()
    out = await _handle_connectors_list(
        hub,
        _NC,
        {"connector": "github", "factory_tenant": "default"},
    )
    assert len(out["installations"]) == 1
    assert out["installations"][0]["external_id"] == "111"


@pytest.mark.asyncio
async def test_connectors_upsert_rejects_tenant_mismatch(
    patch_installation_store,
) -> None:
    hub = MagicMock()
    out = await _handle_connectors_upsert(
        hub,
        _NC,
        {
            "connector": "github",
            "external_id": "99",
            "factory_tenant": "other",
            "operator_tenant": "default",
        },
    )
    assert out["error"] == "tenant_forbidden"


@pytest.mark.asyncio
async def test_connectors_upsert_and_delete_lifecycle(
    patch_installation_store, installation_store
) -> None:
    hub = MagicMock()
    upsert = await _handle_connectors_upsert(
        hub,
        _NC,
        {
            "connector": "cloudflare",
            "external_id": "acct-1",
            "factory_tenant": "default",
            "operator_tenant": "default",
        },
    )
    assert upsert["ok"] is True
    listed = await _handle_connectors_list(
        hub,
        _NC,
        {"connector": "cloudflare", "factory_tenant": "default"},
    )
    assert listed["installations"][0]["enabled"] is True
    deleted = await _handle_connectors_delete(
        hub,
        _NC,
        {
            "connector": "cloudflare",
            "external_id": "acct-1",
            "operator_tenant": "default",
        },
    )
    assert deleted["ok"] is True
    rows = await installation_store.list_rows()
    match = next(r for r in rows if r["external_id"] == "acct-1")
    assert match["enabled"] is False


@pytest.mark.asyncio
async def test_resume_rejects_busy_pool() -> None:
    hub = MagicMock()
    pool = MagicMock()
    pool.is_idle = False
    hub.pools = {"web:smoke:agent:lyra": pool}
    out = await _handle_sessions_resume(
        hub, _NC, {"agent": "lyra", "cli_session_id": "cli-9"}
    )
    assert out["accepted"] is False
    assert "flight" in out["message"]