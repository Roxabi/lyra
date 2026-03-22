"""Tests for PoolManager — covers flush_pool, set_debounce_ms, eviction flush."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from lyra.core.hub import Hub
from lyra.core.message import Platform
from lyra.core.pool import Pool

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
        hub.register_agent(agent)  # type: ignore[arg-type]

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
        hub.register_agent(agent)  # type: ignore[arg-type]

        pool = hub.get_or_create_pool("pool-1", "sdk-agent")
        pool.user_id = "alice"

        # Should not raise even though agent has no flush_session
        await hub.flush_pool("pool-1", "end")
        assert "pool-1" not in hub.pools

    @pytest.mark.asyncio()
    async def test_flush_skips_zero_message_pool(self):
        hub = _make_hub()
        agent = _StubAgent()
        hub.register_agent(agent)  # type: ignore[arg-type]

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
        hub.register_agent(agent)  # type: ignore[arg-type]

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
        hub.register_agent(agent)  # type: ignore[arg-type]

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
        hub.register_agent(agent)  # type: ignore[arg-type]

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
        hub.register_agent(agent)  # type: ignore[arg-type]

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
        hub.register_agent(agent)  # type: ignore[arg-type]

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
        """After TTL eviction, _resume_session_ids[pool_id] is set for next _spawn."""
        from unittest.mock import patch

        from lyra.core.cli_pool import CliPool, _ProcessEntry
        from tests.core.conftest_cli_pool import DEFAULT_MODEL, make_fake_proc

        hub = _make_hub(pool_ttl=0.1)
        cli_pool = CliPool()
        hub.cli_pool = cli_pool

        entry = _ProcessEntry(
            proc=make_fake_proc([]), pool_id="pool-1",
            model_config=DEFAULT_MODEL, session_id="sess-evict-resume",
        )
        cli_pool._entries["pool-1"] = entry

        hub.get_or_create_pool("pool-1", "test-agent")
        hub.pools["pool-1"]._last_active = time.monotonic() - 1.0
        hub._pool_manager._last_eviction_check = 0.0

        with patch.object(cli_pool, "_session_file_exists", return_value=True):
            hub.get_or_create_pool("pool-2", "test-agent")

        assert cli_pool._resume_session_ids.get("pool-1") == "sess-evict-resume"
        assert "pool-1" not in cli_pool._entries

    @pytest.mark.asyncio()
    async def test_eviction_no_session_preserved_when_file_missing(self) -> None:
        """Session file absent at eviction time → _resume_session_ids not populated."""
        from unittest.mock import patch

        from lyra.core.cli_pool import CliPool, _ProcessEntry
        from tests.core.conftest_cli_pool import DEFAULT_MODEL, make_fake_proc

        hub = _make_hub(pool_ttl=0.1)
        cli_pool = CliPool()
        hub.cli_pool = cli_pool

        entry = _ProcessEntry(
            proc=make_fake_proc([]), pool_id="pool-1",
            model_config=DEFAULT_MODEL, session_id="sess-gone",
        )
        cli_pool._entries["pool-1"] = entry

        hub.get_or_create_pool("pool-1", "test-agent")
        hub.pools["pool-1"]._last_active = time.monotonic() - 1.0
        hub._pool_manager._last_eviction_check = 0.0

        with patch.object(cli_pool, "_session_file_exists", return_value=False):
            hub.get_or_create_pool("pool-2", "test-agent")

        assert cli_pool._resume_session_ids.get("pool-1") is None
        assert "pool-1" not in cli_pool._entries

    @pytest.mark.asyncio()
    async def test_flush_pool_does_not_call_sync_evict_entry(self) -> None:
        """flush_pool (intentional disconnect) must not call _sync_evict_entry."""
        hub = _make_hub()
        cli_pool = MagicMock()
        hub.cli_pool = cli_pool
        hub.get_or_create_pool("pool-1", "test-agent")

        await hub._pool_manager.flush_pool("pool-1")

        cli_pool._sync_evict_entry.assert_not_called()
