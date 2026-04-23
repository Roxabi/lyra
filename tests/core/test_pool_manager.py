"""Tests for PoolManager — covers flush_pool, set_debounce_ms, eviction flush."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.cli.cli_pool import CliPool, _ProcessEntry
from lyra.core.hub import Hub
from lyra.core.messaging.message import Platform
from lyra.core.pool import Pool

if TYPE_CHECKING:
    from lyra.core.agent import AgentBase
from tests.core.conftest_cli_pool import (
    _PATCH_TARGET,
    ASSISTANT_LINE,
    DEFAULT_MODEL,
    INIT_LINE,
    RESULT_LINE,
    make_fake_proc,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubAgent:
    """Agent with a trackable flush_session method."""

    def __init__(self, name: str = "test-agent") -> None:
        self.name = name
        self.flush_calls: list[tuple[Pool, str]] = []

    async def flush_session(self, pool: Pool, reason: str) -> None:
        self.flush_calls.append((pool, reason))


class _AgentNoFlush:
    """Agent without flush_session (SDK-style)."""

    def __init__(self, name: str = "sdk-agent") -> None:
        self.name = name


def _make_hub(pool_ttl: float = 3600.0, debounce_ms: int = 0) -> Hub:
    hub = Hub(pool_ttl=pool_ttl, debounce_ms=debounce_ms)
    hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
    return hub


# ---------------------------------------------------------------------------
# flush_pool
# ---------------------------------------------------------------------------


class TestFlushPool:
    """PoolManager.flush_pool() — adapter disconnect flow."""

    @pytest.mark.asyncio()
    async def test_flush_calls_agent_flush_session(self):
        hub = _make_hub()
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        pool = hub.get_or_create_pool("pool-1", "test-agent")
        pool.user_id = "alice"  # simulate that a message was received

        await hub.flush_pool("pool-1", "end")

        assert len(agent.flush_calls) == 1
        flushed_pool, reason = agent.flush_calls[0]
        assert flushed_pool is pool
        assert reason == "end"
        # Pool removed from registry
        assert "pool-1" not in hub.pools

    @pytest.mark.asyncio()
    async def test_flush_nonexistent_pool_is_noop(self):
        hub = _make_hub()
        # Should not raise
        await hub.flush_pool("nonexistent")

    @pytest.mark.asyncio()
    async def test_flush_skips_agent_without_flush_session(self):
        hub = _make_hub()
        agent = _AgentNoFlush()
        hub.register_agent(cast("AgentBase", agent))

        pool = hub.get_or_create_pool("pool-1", "sdk-agent")
        pool.user_id = "alice"

        # Should not raise even though agent has no flush_session
        await hub.flush_pool("pool-1", "end")
        assert "pool-1" not in hub.pools

    @pytest.mark.asyncio()
    async def test_flush_skips_zero_message_pool(self):
        hub = _make_hub()
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        # Pool with user_id="" (no messages received)
        hub.get_or_create_pool("pool-1", "test-agent")

        await hub.flush_pool("pool-1", "end")
        # flush_session should NOT be called for zero-message pools
        assert len(agent.flush_calls) == 0


# ---------------------------------------------------------------------------
# set_debounce_ms
# ---------------------------------------------------------------------------


class TestSetDebounceMs:
    """PoolManager.set_debounce_ms() — update on live pools and hub default."""

    def test_updates_hub_default(self):
        hub = _make_hub(debounce_ms=100)
        assert hub._debounce_ms == 100

        hub.set_debounce_ms(500)
        assert hub._debounce_ms == 500

    def test_updates_existing_pools(self):
        hub = _make_hub(debounce_ms=100)
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        pool1 = hub.get_or_create_pool("pool-1", "test-agent")
        pool2 = hub.get_or_create_pool("pool-2", "test-agent")
        assert pool1.debounce_ms == 100
        assert pool2.debounce_ms == 100

        hub.set_debounce_ms(500)
        assert pool1.debounce_ms == 500
        assert pool2.debounce_ms == 500

    def test_new_pools_use_updated_value(self):
        hub = _make_hub(debounce_ms=100)
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        hub.set_debounce_ms(500)
        pool = hub.get_or_create_pool("pool-1", "test-agent")
        assert pool.debounce_ms == 500


# ---------------------------------------------------------------------------
# Stale Pool Eviction with flush_session
# ---------------------------------------------------------------------------


class TestEvictionFlushSession:
    """Stale pool eviction triggers async flush_session for pools with messages."""

    @pytest.mark.asyncio()
    async def test_eviction_calls_flush_session(self):
        hub = _make_hub(pool_ttl=0.1)
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        pool = hub.get_or_create_pool("pool-1", "test-agent")
        pool.user_id = "alice"  # simulate message received

        # Make pool stale by backdating last_active
        pool._last_active = time.monotonic() - 1.0
        # Reset eviction throttle so next call actually scans
        hub._pool_manager._last_eviction_check = 0.0

        # Trigger eviction via get_or_create_pool
        hub.get_or_create_pool("pool-2", "test-agent")

        # flush_session is fire-and-forget via create_task — let it run
        await asyncio.sleep(0.05)

        assert len(agent.flush_calls) == 1
        _, reason = agent.flush_calls[0]
        assert reason == "idle"
        assert "pool-1" not in hub.pools

    @pytest.mark.asyncio()
    async def test_eviction_skips_zero_message_pool(self):
        hub = _make_hub(pool_ttl=0.1)
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        pool = hub.get_or_create_pool("pool-1", "test-agent")
        # user_id="" — no messages, should not flush
        pool._last_active = time.monotonic() - 1.0
        hub._pool_manager._last_eviction_check = 0.0

        hub.get_or_create_pool("pool-2", "test-agent")
        await asyncio.sleep(0.05)

        assert len(agent.flush_calls) == 0

    @pytest.mark.asyncio()
    async def test_eviction_cleans_cli_pool_entries(self):
        hub = _make_hub(pool_ttl=0.1)
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        cli_pool = MagicMock()
        hub.cli_pool = cli_pool

        pool = hub.get_or_create_pool("pool-1", "test-agent")
        pool._last_active = time.monotonic() - 1.0
        hub._pool_manager._last_eviction_check = 0.0

        hub.get_or_create_pool("pool-2", "test-agent")

        cli_pool._sync_evict_entry.assert_called_once_with(
            "pool-1", preserve_session=True
        )


# ---------------------------------------------------------------------------
# TestEvictionSessionPreserve — #370
# ---------------------------------------------------------------------------


class TestEvictionSessionPreserve:
    """TTL eviction must preserve session_id in _resume_session_ids for auto-resume."""

    @pytest.mark.asyncio()
    async def test_eviction_preserves_session_for_auto_resume(self) -> None:
        """After TTL eviction: session preserved AND next _spawn passes --resume.

        SC3: _resume_session_ids populated before entry removed.
        SC4/SC9: next send() triggers _spawn with --resume <session_id> and
                 consumes the intent (one-shot).
        SC5: FRESH notification implicitly suppressed — --resume causes CLI to
             resume the existing session rather than starting fresh.
        """
        hub = _make_hub(pool_ttl=0.1)
        cli_pool = CliPool()
        hub.cli_pool = cli_pool

        entry = _ProcessEntry(
            proc=make_fake_proc([]),
            pool_id="pool-1",
            model_config=DEFAULT_MODEL,
            session_id="sess-evict-resume",
        )
        cli_pool._entries["pool-1"] = entry

        hub.get_or_create_pool("pool-1", "test-agent")
        hub.pools["pool-1"]._last_active = time.monotonic() - 1.0
        hub._pool_manager._last_eviction_check = 0.0

        # Evict: session_id preserved before entry removed (SC3)
        hub.get_or_create_pool("pool-2", "test-agent")

        assert cli_pool._resume_session_ids.get("pool-1") == "sess-evict-resume"
        assert "pool-1" not in cli_pool._entries

        # Next spawn: --resume passed to CLI, intent consumed (SC4/SC9)
        resumed_proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        with patch(
            _PATCH_TARGET, new=AsyncMock(return_value=resumed_proc)
        ) as mock_spawn:  # noqa: E501
            await cli_pool.send("pool-1", "hello", DEFAULT_MODEL)

        cmd_args = list(mock_spawn.call_args[0])
        assert "--resume" in cmd_args, f"--resume missing in {cmd_args}"
        assert cmd_args[cmd_args.index("--resume") + 1] == "sess-evict-resume"
        # One-shot: intent consumed after spawn
        assert cli_pool._resume_session_ids.get("pool-1") is None

    @pytest.mark.asyncio()
    async def test_flush_pool_does_not_call_sync_evict_entry(self) -> None:
        """flush_pool (intentional disconnect) must not call _sync_evict_entry."""
        hub = _make_hub()
        cli_pool = MagicMock()
        hub.cli_pool = cli_pool
        hub.get_or_create_pool("pool-1", "test-agent")

        await hub._pool_manager.flush_pool("pool-1")

        cli_pool._sync_evict_entry.assert_not_called()


# ---------------------------------------------------------------------------
# Thread Safety — Lock Tests
# ---------------------------------------------------------------------------


class TestLockSafetyConcurrentMutation:
    """Concurrent pop + iterate must not raise RuntimeError."""

    def test_concurrent_pop_and_iterate_no_runtime_error(self):
        """100 threads (50 iterate + 50 pop) — no RuntimeError, final count OK."""
        hub = _make_hub()
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        # Create initial pools
        for i in range(50):
            hub.get_or_create_pool(f"pool-{i}", "test-agent")

        errors: list[Exception] = []

        def iterate_pools():
            try:
                for _ in range(100):
                    _ = list(hub.pools.keys())
            except Exception as e:
                errors.append(e)

        def pop_pools():
            try:
                for i in range(50, 100):
                    hub.get_or_create_pool(f"pool-{i}", "test-agent")
            except Exception as e:
                errors.append(e)

        import threading

        threads = [threading.Thread(target=iterate_pools) for _ in range(50)] + [
            threading.Thread(target=pop_pools) for _ in range(50)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Exceptions raised: {errors}"
        assert len(hub.pools) <= hub._max_pools


# ---------------------------------------------------------------------------
# LRU Eviction Tests
# ---------------------------------------------------------------------------


class TestLruEvictionAtCapacity:
    """LRU eviction when at max_pools capacity."""

    def test_evicts_oldest_at_capacity(self):
        """When at max_pools, next create evicts oldest (leftmost in OrderedDict)."""
        hub = _make_hub()
        hub._max_pools = 3  # override default
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        # Create pools A, B, C (in order)
        pool_a = hub.get_or_create_pool("pool-a", "test-agent")
        pool_a.user_id = "user-a"
        pool_b = hub.get_or_create_pool("pool-b", "test-agent")
        pool_b.user_id = "user-b"
        pool_c = hub.get_or_create_pool("pool-c", "test-agent")
        pool_c.user_id = "user-c"

        assert len(hub.pools) == 3

        # Touch pool B via public API (makes it most recent in LRU)
        hub._pool_manager.touch_pool("pool-b")

        # Create pool D → should evict A (oldest)
        pool_d = hub.get_or_create_pool("pool-d", "test-agent")
        pool_d.user_id = "user-d"

        assert "pool-a" not in hub.pools, "pool-a should be evicted (LRU)"
        assert "pool-b" in hub.pools
        assert "pool-c" in hub.pools
        assert "pool-d" in hub.pools
        assert len(hub.pools) == 3

    def test_lru_order_preserved_after_touch(self):
        """Touch updates LRU order — touched pool moves to end."""
        hub = _make_hub()
        hub._max_pools = 3
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        # Create A, B, C
        hub.get_or_create_pool("pool-a", "test-agent")
        hub.get_or_create_pool("pool-b", "test-agent")
        hub.get_or_create_pool("pool-c", "test-agent")

        # Touch A via get_or_create_pool (makes it most recent in LRU)
        hub.get_or_create_pool("pool-a", "test-agent")

        # Verify behavior: when at capacity, oldest-accessed pool (B) is evicted
        hub._max_pools = 2  # force eviction on next create
        hub._pool_manager.touch_pool("pool-a")  # A is now most recent
        hub.get_or_create_pool("pool-d", "test-agent")  # should evict B (oldest)
        assert "pool-b" not in hub.pools, "pool-b should be evicted (oldest in LRU)"
        assert "pool-a" in hub.pools  # A was touched, so kept


# ---------------------------------------------------------------------------
# Max Pools Bound Tests
# ---------------------------------------------------------------------------


class TestMaxPoolsNeverExceeded:
    """Pool count never exceeds max_pools."""

    def test_high_load_never_exceeds_max_pools(self):
        """100 threads creating pools — count never exceeds max_pools."""
        hub = _make_hub()
        hub._max_pools = 10
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        import threading

        barrier = threading.Barrier(100)

        def create_pool(i: int):
            barrier.wait()
            hub.get_or_create_pool(f"pool-{i}", "test-agent")

        threads = [threading.Thread(target=create_pool, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(hub.pools) <= 10, f"pool count {len(hub.pools)} exceeds max_pools=10"

    def test_sequential_creation_respects_cap(self):
        """Sequential creation at capacity triggers eviction."""
        hub = _make_hub()
        hub._max_pools = 2
        agent = _StubAgent()
        hub.register_agent(cast("AgentBase", agent))

        hub.get_or_create_pool("pool-1", "test-agent")
        hub.get_or_create_pool("pool-2", "test-agent")
        assert len(hub.pools) == 2

        # Third pool → evicts oldest
        hub.get_or_create_pool("pool-3", "test-agent")
        assert len(hub.pools) == 2
        assert "pool-1" not in hub.pools


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------


class TestMaxPoolsValidation:
    """max_pools <= 0 raises ValueError at Hub init."""

    def test_zero_max_pools_raises_valueerror(self):
        with pytest.raises(ValueError, match="max_pools must be > 0"):
            Hub(max_pools=0)

    def test_negative_max_pools_raises_valueerror(self):
        with pytest.raises(ValueError, match="max_pools must be > 0"):
            Hub(max_pools=-1)

    def test_positive_max_pools_works(self):
        hub = Hub(max_pools=100)
        assert hub._max_pools == 100
