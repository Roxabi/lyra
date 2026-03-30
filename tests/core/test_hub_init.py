"""Tests for Hub init, adapter registration, Pool, and Agent structures."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from lyra.core import (
    Agent,
    AgentBase,
    Hub,
    Pool,
)
from lyra.core.message import (
    Platform,
)
from tests.core.conftest import MockAdapter

# ---------------------------------------------------------------------------
# Pool (unchanged — no dependency on new API)
# ---------------------------------------------------------------------------


class TestPool:
    def _make_hub(self) -> MagicMock:
        hub = MagicMock()
        hub.agent_registry = {}
        hub.circuit_registry = None
        hub._msg_manager = None
        return hub

    def test_initial_state(self) -> None:
        hub = self._make_hub()
        pool = Pool(pool_id="telegram:main:alice", agent_name="lyra", ctx=hub)
        assert pool.pool_id == "telegram:main:alice"
        assert pool.agent_name == "lyra"
        assert pool.history == []
        # Verify each pool gets its own list (not a shared mutable default)
        pool2 = Pool(pool_id="telegram:main:bob", agent_name="lyra", ctx=hub)
        assert pool.history is not pool2.history

    def test_has_inbox_queue(self) -> None:
        hub = self._make_hub()
        pool = Pool(pool_id="p1", agent_name="lyra", ctx=hub)
        assert isinstance(pool._inbox, asyncio.Queue)

    def test_inbox_is_per_pool(self) -> None:
        hub = self._make_hub()
        p1 = Pool(pool_id="p1", agent_name="lyra", ctx=hub)
        p2 = Pool(pool_id="p2", agent_name="lyra", ctx=hub)
        assert p1._inbox is not p2._inbox

    def test_current_task_starts_none(self) -> None:
        hub = self._make_hub()
        pool = Pool(pool_id="p1", agent_name="lyra", ctx=hub)
        assert pool._current_task is None


# ---------------------------------------------------------------------------
# Agent (unchanged)
# ---------------------------------------------------------------------------


class TestAgent:
    def test_mutable_for_hot_reload(self) -> None:
        agent = Agent(
            name="lyra", system_prompt="You are Lyra.", memory_namespace="lyra"
        )
        agent.name = "other"
        assert agent.name == "other"

    def test_default_permissions(self) -> None:
        agent = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        assert agent.permissions == ()

    def test_agent_base_cannot_be_instantiated_directly(self) -> None:
        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        with pytest.raises(TypeError):
            cls: Any = AgentBase
            cls(config)  # deliberately testing runtime TypeError


# ---------------------------------------------------------------------------
# Hub — init
# ---------------------------------------------------------------------------


class TestHubInit:
    def test_inbound_bus_exists(self) -> None:
        from lyra.core.inbound_bus import LocalBus

        hub = Hub()
        assert Hub.BUS_SIZE == 100
        assert isinstance(hub.inbound_bus, LocalBus)
        # hub.bus property was removed in #48 — no raw asyncio.Queue exposure
        assert not hasattr(hub, "bus")

    def test_per_platform_queue_is_bounded_after_register_adapter(self) -> None:
        hub = Hub()
        hub.register_adapter(Platform.TELEGRAM, "main", MockAdapter())
        assert hub.inbound_bus.qsize(Platform.TELEGRAM) == 0
        # Per-platform queue has BUS_SIZE maxsize
        assert hub.inbound_bus._queues[Platform.TELEGRAM].maxsize == Hub.BUS_SIZE

    def test_empty_registries(self) -> None:
        hub = Hub()
        assert hub.adapter_registry == {}
        assert hub.bindings == {}
        assert hub.pools == {}


# ---------------------------------------------------------------------------
# T4 — Hub adapter registry (new (Platform, str) key)
# ---------------------------------------------------------------------------


class TestRegisterAdapter:
    def test_registers_by_platform_and_bot_id(self) -> None:
        hub = Hub()
        a = MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", a)
        assert hub.adapter_registry[(Platform.TELEGRAM, "main")] is a

    def test_two_bots_same_platform_no_collision(self) -> None:
        hub = Hub()
        a1, a2 = MockAdapter(), MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "bot1", a1)
        hub.register_adapter(Platform.TELEGRAM, "bot2", a2)
        assert hub.adapter_registry[(Platform.TELEGRAM, "bot1")] is a1
        assert hub.adapter_registry[(Platform.TELEGRAM, "bot2")] is a2

    def test_overwrite_adapter(self) -> None:
        hub = Hub()
        a1, a2 = MockAdapter(), MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", a1)
        hub.register_adapter(Platform.TELEGRAM, "main", a2)
        assert hub.adapter_registry[(Platform.TELEGRAM, "main")] is a2
