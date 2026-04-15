"""Tests for Hub lifecycle: pool TTL eviction, memory fields, flush tasks, circuit failure recording."""  # noqa: E501

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core import Agent, AgentBase, Hub, Pool
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.message import InboundMessage, Response
from lyra.core.render_events import RenderEvent
from tests.core.conftest import make_inbound_message

# ---------------------------------------------------------------------------
# Pool TTL eviction (#205)
# ---------------------------------------------------------------------------


class TestPoolTTLEviction:
    """PoolManager._evict_stale_pools removes idle pools exceeding the TTL."""

    @staticmethod
    def _force_eviction_eligible(hub: Hub) -> None:
        """Reset throttle so the next get_or_create_pool triggers eviction."""
        hub._pool_manager._last_eviction_check = 0.0

    def test_stale_idle_pool_evicted(self) -> None:
        """An idle pool past TTL is removed on next get_or_create_pool call."""
        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 120
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p2", "agent")
        assert "p1" not in hub.pools
        assert "p2" in hub.pools

    def test_active_pool_not_evicted(self) -> None:
        """A pool with a running task is never evicted, even past TTL."""
        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 120
        pool._current_task = MagicMock()
        pool._current_task.done.return_value = False
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p2", "agent")
        assert "p1" in hub.pools  # not evicted — still active

    def test_fresh_pool_not_evicted(self) -> None:
        """A recently active idle pool is kept."""
        hub = Hub(pool_ttl=60)
        hub.get_or_create_pool("p1", "agent")
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p2", "agent")
        assert "p1" in hub.pools

    def test_pool_ttl_default(self) -> None:
        """Default POOL_TTL is 604800s (7 days) and passes through to _pool_ttl."""
        hub = Hub()
        assert hub._pool_ttl == Hub.POOL_TTL

    def test_done_task_pool_evicted(self) -> None:
        """A pool whose task finished (done()=True) is evicted when stale."""
        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 120
        pool._current_task = MagicMock()
        pool._current_task.done.return_value = True
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p2", "agent")
        assert "p1" not in hub.pools  # evicted — task is done

    def test_multiple_stale_pools_all_evicted(self) -> None:
        """Multiple stale idle pools are evicted in a single pass."""
        hub = Hub(pool_ttl=60)
        p1 = hub.get_or_create_pool("p1", "agent")
        hub.get_or_create_pool("p2", "agent")
        p3 = hub.get_or_create_pool("p3", "agent")
        p1._last_active -= 120
        p3._last_active -= 120
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p4", "agent")
        assert "p1" not in hub.pools
        assert "p3" not in hub.pools
        assert "p2" in hub.pools
        assert "p4" in hub.pools

    def test_eviction_throttled(self) -> None:
        """Eviction scan is throttled — skipped if less than TTL/10 elapsed."""
        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 120
        # Don't reset throttle — second call is within TTL/10
        hub.get_or_create_pool("p2", "agent")
        assert "p1" in hub.pools  # not evicted — throttled

    async def test_submit_refreshes_last_active(self) -> None:
        """Pool.submit() updates last_active timestamp."""
        import time

        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 50  # nearly stale
        t0 = time.monotonic()
        msg = make_inbound_message()
        pool.submit(msg)
        assert pool.last_active >= t0
        pool.cancel()


# ---------------------------------------------------------------------------
# S2 — Hub._memory + _memory_tasks fields (issue #83)
# ---------------------------------------------------------------------------


class TestHubMemoryFields:
    """Hub must carry a MemoryManager reference and a set of pending tasks (S2)."""

    def test_hub_has_memory_field(self) -> None:
        """Hub must expose a _memory attribute, defaulting to None."""
        hub = Hub()
        assert hasattr(hub, "_memory")  # FAILS until field added
        assert hub._memory is None

    def test_hub_has_memory_tasks_set(self) -> None:
        """Hub must expose a _memory_tasks attribute as a set."""
        hub = Hub()
        assert hasattr(hub, "_memory_tasks")  # FAILS until field added
        assert isinstance(hub._memory_tasks, set)

    def test_register_agent_injects_memory(self) -> None:
        """register_agent() must inject hub._memory into the agent's _memory."""
        hub = Hub()
        mock_mm = MagicMock()
        object.__setattr__(hub, "_memory", mock_mm)

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = ConcreteAgent(config)
        hub.register_agent(agent)

        # After registration, agent._memory must point to hub._memory
        assert agent._memory is mock_mm  # FAILS until register_agent() injects memory

    def test_register_agent_skips_injection_when_memory_is_none(self) -> None:
        """register_agent() must not fail if hub._memory is None
        (memory not configured)."""

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        hub = Hub()
        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = ConcreteAgent(config)
        # Must not raise even when _memory is None
        hub.register_agent(agent)


# ---------------------------------------------------------------------------
# S4 — Hub eviction creates flush tasks + shutdown drains (issue #83)
# ---------------------------------------------------------------------------


class TestHubEvictFlushTask:
    """Hub._evict_stale_pools() must create flush tasks for pools with messages (S4)."""

    @pytest.mark.asyncio
    async def test_evict_stale_pool_creates_flush_task(self) -> None:
        """Evicting a pool that has at least one message must schedule a flush task."""
        hub = Hub(pool_ttl=1)
        mock_mm = AsyncMock()
        object.__setattr__(hub, "_memory", mock_mm)

        # Register a mock agent with flush_session so eviction creates a task
        mock_agent = MagicMock()
        mock_agent.name = "agent"
        mock_agent.flush_session = AsyncMock()
        hub.register_agent(mock_agent)

        pool = hub.get_or_create_pool("p_flush", "agent")
        pool._last_active -= 5  # force stale
        # Simulate the pool having had a message (user_id is set)
        pool.user_id = "u1"

        # Reset throttle and trigger eviction scan
        hub._pool_manager._last_eviction_check = 0.0
        hub._pool_manager._evict_stale_pools()

        # Eviction must schedule a flush task for the pool with messages
        assert len(hub._memory_tasks) >= 1

    @pytest.mark.asyncio
    async def test_shutdown_closes_injected_stores(self) -> None:
        """hub.shutdown() must close turn_store and message_index when injected."""
        hub = Hub()
        mock_turn = AsyncMock()
        mock_index = AsyncMock()
        hub.set_turn_store(mock_turn)
        hub.set_message_index(mock_index)
        await hub.shutdown()
        mock_turn.close.assert_awaited_once()
        mock_index.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# SC-10 — record_circuit_failure records anthropic CB for ProviderError subclasses
# ---------------------------------------------------------------------------


class TestRecordCircuitFailure:
    def test_provider_error_subclass_records_anthropic_cb(self) -> None:
        """ProviderAuthError (subclass) trips both hub and anthropic CBs."""
        from lyra.errors import ProviderAuthError

        hub_cb = CircuitBreaker("hub", failure_threshold=5)
        ant_cb = CircuitBreaker("anthropic", failure_threshold=5)
        registry = CircuitRegistry()
        registry.register(hub_cb)
        registry.register(ant_cb)
        hub = Hub(circuit_registry=registry)

        hub.record_circuit_failure(ProviderAuthError("bad key", status_code=401))

        assert hub_cb._failure_count == 1
        assert ant_cb._failure_count == 1

    def test_runtime_error_does_not_record_anthropic_cb(self) -> None:
        """Plain RuntimeError trips hub CB only, not anthropic CB."""
        hub_cb = CircuitBreaker("hub", failure_threshold=5)
        ant_cb = CircuitBreaker("anthropic", failure_threshold=5)
        registry = CircuitRegistry()
        registry.register(hub_cb)
        registry.register(ant_cb)
        hub = Hub(circuit_registry=registry)

        hub.record_circuit_failure(RuntimeError("boom"))

        assert hub_cb._failure_count == 1
        assert ant_cb._failure_count == 0
