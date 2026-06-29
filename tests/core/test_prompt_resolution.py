"""Tests for resolve_effective_system_prompt."""

from __future__ import annotations

from unittest.mock import MagicMock

from factory.core.agent.agent_config import Agent
from factory.core.pool import Pool
from factory.core.prompt_resolution import (
    resolve_agent_runtime_defaults,
    resolve_effective_system_prompt,
)


def test_resolve_effective_prefers_pool_cache() -> None:
    agent = Agent(name="a", system_prompt="from-agent", memory_namespace="a")
    pool = Pool(pool_id="p", agent_name="a", ctx=MagicMock())
    pool._system_prompt = "from-pool"
    assert resolve_effective_system_prompt(agent, pool) == "from-pool"


def test_resolve_effective_falls_back_to_agent() -> None:
    agent = Agent(name="a", system_prompt="from-agent", memory_namespace="a")
    pool = Pool(pool_id="p", agent_name="a", ctx=MagicMock())
    assert resolve_effective_system_prompt(agent, pool) == "from-agent"


def test_resolve_agent_runtime_defaults() -> None:
    assert resolve_agent_runtime_defaults(backend="omp-rpc", model="grok") == {
        "backend": "omp-rpc",
        "model": "grok",
    }