"""Tests for session catalog agent resolution (#1771)."""

from __future__ import annotations

from factory.core.hub.hub_protocol import Binding, RoutingKey
from factory.core.hub.session_catalog import agent_for_pool, parse_pool_id
from factory.core.messaging.message import Platform


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