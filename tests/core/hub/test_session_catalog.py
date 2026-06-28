"""Tests for session catalog agent resolution and list_sessions_for_agent (#1771)."""

from __future__ import annotations

import pytest

from factory.core.hub.hub_protocol import Binding, RoutingKey
from factory.core.hub.session_catalog import (
    agent_for_pool,
    list_sessions_for_agent,
    parse_pool_id,
)
from factory.core.messaging.message import Platform
from factory.core.stores.turn_store_protocol import CatalogSessionRow


def test_parse_pool_id_with_scope_colons() -> None:
    p, b, s = parse_pool_id("telegram:main:chat:42")
    assert p == "telegram"
    assert b == "main"
    assert s == "chat:42"


def test_agent_for_pool_wildcard_binding() -> None:
    bindings = {
        RoutingKey(Platform.TELEGRAM, "main", "*"): Binding(
            agent_name="lyra", pool_id="telegram:main:*"
        ),
    }
    assert agent_for_pool("telegram:main:chat:99", bindings) == "lyra"


def test_agent_for_pool_exact_web_binding() -> None:
    bindings = {
        RoutingKey(Platform.WEB, "smoke", "agent:lyra"): Binding(
            agent_name="lyra", pool_id="web:smoke:agent:lyra"
        ),
    }
    assert agent_for_pool("web:smoke:agent:lyra", bindings) == "lyra"


def _row(
    *,
    session_id: str,
    pool_id: str,
    platform: str,
    last_active_at: str,
) -> CatalogSessionRow:
    return {
        "session_id": session_id,
        "pool_id": pool_id,
        "platform": platform,
        "cli_session_id": f"cli-{session_id}",
        "first_user_msg": "hi",
        "turn_count": 1,
        "last_active_at": last_active_at,
    }


class _CatalogStore:
    """In-memory TurnStoreProtocol stub for catalog unit tests."""

    def __init__(self, rows: list[CatalogSessionRow]) -> None:
        self._rows = rows
        self.last_scan_limit: int | None = None

    async def get_turns(self, pool_id: str, user_id: str, limit: int = 50) -> list:
        return []

    async def list_sessions(self, pool_id: str, limit: int = 20) -> list:
        return []

    async def get_cli_session(self, session_id: str) -> str | None:
        return None

    async def get_cli_session_by_pool(self, pool_id: str) -> str | None:
        return None

    async def get_last_session(self, pool_id: str) -> str | None:
        return None

    async def list_recent_sessions(self, limit: int = 200) -> list[CatalogSessionRow]:
        self.last_scan_limit = limit
        return list(self._rows)

    async def get_turns_by_session(self, session_id: str, limit: int = 50) -> list:
        return []


@pytest.mark.asyncio
async def test_list_sessions_for_agent_filters_sorts_and_limits() -> None:
    bindings = {
        RoutingKey(Platform.TELEGRAM, "main", "*"): Binding(
            agent_name="lyra", pool_id="telegram:main:*"
        ),
        RoutingKey(Platform.WEB, "smoke", "agent:lyra"): Binding(
            agent_name="lyra", pool_id="web:smoke:agent:lyra"
        ),
        RoutingKey(Platform.DISCORD, "main", "*"): Binding(
            agent_name="other", pool_id="discord:main:*"
        ),
    }
    store = _CatalogStore(
        [
            _row(
                session_id="tg-old",
                pool_id="telegram:main:chat:1",
                platform="telegram",
                last_active_at="2026-06-27T10:00:00Z",
            ),
            _row(
                session_id="web-new",
                pool_id="web:smoke:agent:lyra",
                platform="web",
                last_active_at="2026-06-28T12:00:00Z",
            ),
            _row(
                session_id="dc-skip",
                pool_id="discord:main:chan:9",
                platform="discord",
                last_active_at="2026-06-28T13:00:00Z",
            ),
            _row(
                session_id="tg-new",
                pool_id="telegram:main:chat:2",
                platform="telegram",
                last_active_at="2026-06-28T11:00:00Z",
            ),
        ]
    )

    out = await list_sessions_for_agent(
        store, bindings, "lyra", limit=2, scan_limit=50
    )

    assert store.last_scan_limit == 50
    assert [r["session_id"] for r in out] == ["web-new", "tg-new"]
    assert all(agent_for_pool(r["pool_id"], bindings) == "lyra" for r in out)


@pytest.mark.asyncio
async def test_list_sessions_for_agent_empty_when_no_bindings_match() -> None:
    store = _CatalogStore(
        [
            _row(
                session_id="orphan",
                pool_id="telegram:unknown:chat:1",
                platform="telegram",
                last_active_at="2026-06-28T00:00:00Z",
            ),
        ]
    )
    bindings = {
        RoutingKey(Platform.WEB, "smoke", "agent:lyra"): Binding(
            agent_name="lyra", pool_id="web:smoke:agent:lyra"
        ),
    }

    out = await list_sessions_for_agent(store, bindings, "lyra", limit=10)

    assert out == []