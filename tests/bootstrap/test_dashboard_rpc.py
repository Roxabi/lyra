"""Unit tests for hub dashboard NATS RPC handlers (#1771)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard_rpc import (
    _handle_agents_status,
    _handle_sessions_list,
    _handle_sessions_resume,
    _handle_sessions_turns,
)
from factory.core.hub.hub_protocol import Binding, RoutingKey
from factory.core.messaging.message import Platform


@pytest.mark.asyncio
async def test_sessions_list_empty_without_turn_store() -> None:
    hub = MagicMock()
    hub._turn_store = None
    hub.bindings = {}
    out = await _handle_sessions_list(hub, {"agent": "lyra", "limit": 10})
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
    out = await _handle_sessions_list(hub, {"agent": "lyra", "limit": 5})
    assert len(out["sessions"]) == 1
    assert out["sessions"][0]["platform"] == "web"
    store.list_recent_sessions.assert_awaited_once()


@pytest.mark.asyncio
async def test_agents_status_marks_offline_without_heartbeat() -> None:
    hub = MagicMock()
    hub.agent_registry = ["lyra"]
    hub._dashboard_worker_freshness = {}
    out = await _handle_agents_status(hub, {"agents": ["lyra"]})
    assert out["agents"][0]["online"] is False


@pytest.mark.asyncio
async def test_agents_status_respects_harness_selection() -> None:
    hub = MagicMock()
    hub.agent_registry = ["lyra"]
    hub._dashboard_worker_freshness = {"clipool-worker": 0.0}
    import time

    hub._dashboard_worker_freshness["clipool-worker"] = time.monotonic()
    out = await _handle_agents_status(
        hub,
        {"agents": ["lyra"], "harness_by_agent": {"lyra": "omp-rpc"}},
    )
    assert out["agents"][0]["harness"] == "omp-rpc"
    assert out["agents"][0]["online"] is False


@pytest.mark.asyncio
async def test_sessions_turns_empty_without_turn_store() -> None:
    hub = MagicMock()
    hub._turn_store = None
    out = await _handle_sessions_turns(hub, {"session_id": "s1", "limit": 50})
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
    out = await _handle_sessions_turns(hub, {"session_id": "s1", "limit": 50})
    assert len(out["turns"]) == 2
    assert out["turns"][0]["content"] == "hi"


@pytest.mark.asyncio
async def test_resume_rejects_busy_pool() -> None:
    hub = MagicMock()
    pool = MagicMock()
    pool.is_idle = False
    hub.pools = {"web:smoke:agent:lyra": pool}
    out = await _handle_sessions_resume(
        hub, {"agent": "lyra", "cli_session_id": "cli-9"}
    )
    assert out["accepted"] is False
    assert "flight" in out["message"]