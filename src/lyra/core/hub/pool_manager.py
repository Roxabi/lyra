"""Pool lifecycle management: creation, eviction, flush."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from typing import TYPE_CHECKING

from ..config import PoolConfig
from ..pool import Pool

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)


class PoolManager:
    """Manages pool lifecycle: creation, eviction, flush.

    Thread-safe via `_lock`. All pool mutations and iterations are guarded.
    LRU eviction via OrderedDict (move-to-end on hit, pop-left on capacity).
    """

    def __init__(self, hub: Hub, pool_config: PoolConfig) -> None:
        self._hub = hub
        self._pool_config = pool_config
        self._pools: OrderedDict[str, Pool] = OrderedDict()
        self._lock = threading.Lock()
        self._last_eviction_check: float = 0.0

    @property
    def pools(self) -> dict[str, Pool]:
        """Read-only view of pools for backward compat."""
        with self._lock:
            return dict(self._pools)

    def get_or_create_pool(self, pool_id: str, agent_name: str) -> Pool:
        """Return existing pool or create a new one.

        Lazily evicts idle pools (TTL) and enforces max_pools cap (LRU).
        Thread-safe: all mutations guarded by `_lock`.
        """
        with self._lock:
            self._evict_stale_pools()

            if pool_id in self._pools:
                pool = self._pools[pool_id]
                pool._touch()
                self._pools.move_to_end(pool_id)  # LRU: most recent
                return pool

            # Enforce max_pools cap before creating new pool
            max_pools = self._hub._max_pools
            while len(self._pools) >= max_pools:
                self._evict_lru_locked()

            new_pool = Pool(
                pool_id=pool_id,
                agent_name=agent_name,
                ctx=self._hub,
                config=self._pool_config,
            )
            if self._hub._turn_store is not None:
                new_pool._observer.register_turn_store(self._hub._turn_store)
            if self._hub._message_index is not None:
                new_pool._observer.register_message_index(self._hub._message_index)
            self._pools[pool_id] = new_pool
            return new_pool

    def _evict_stale_pools(self) -> None:
        """Remove idle pools whose last activity exceeds the TTL.

        Called inside lock context. Throttled: skips the scan if less than
        TTL/10 has elapsed since the last check.
        """
        now = time.monotonic()
        if (now - self._last_eviction_check) < self._hub._pool_ttl / 10:
            return
        self._last_eviction_check = now

        stale = [
            pid
            for pid, pool in self._pools.items()
            if pool.is_idle and (now - pool.last_active) > self._hub._pool_ttl
        ]
        for pid in stale:
            self._evict_pool_locked(pid, reason="idle")

        if stale:
            log.info("evicted %d stale pool(s)", len(stale))
            log.debug("evicted pool IDs: %s", stale)

    def _evict_lru_locked(self) -> None:
        """Evict least-recently-used pool. Called inside lock at capacity.

        Uses popitem(last=False) to get the leftmost (oldest) entry.
        """
        if not self._pools:
            return
        pid, _ = self._pools.popitem(last=False)
        self._evict_pool_locked(pid, reason="lru")
        log.debug("LRU evicted pool: %s", pid)

    def _evict_pool_locked(self, pool_id: str, reason: str) -> Pool | None:
        """Evict a pool by ID. Called inside lock. Returns evicted pool or None."""
        pool = self._pools.pop(pool_id, None)
        if pool is None:
            return None

        if pool.user_id:  # skip zero-message pools
            agent = self._hub.agent_registry.get(pool.agent_name)
            if agent is not None and hasattr(agent, "flush_session"):
                task = asyncio.create_task(agent.flush_session(pool, reason))
                self._hub._memory_tasks.add(task)

                def _on_flush_done(t: asyncio.Task, _pid: str = pool_id) -> None:
                    self._hub._memory_tasks.discard(t)
                    if not t.cancelled() and t.exception():
                        log.error(
                            "flush_session failed during eviction (pool=%s): %s",
                            _pid,
                            t.exception(),
                        )

                task.add_done_callback(_on_flush_done)

        # Evict CLI entry synchronously so a new pool can't claim the old process
        if self._hub.cli_pool is not None:
            self._hub.cli_pool._sync_evict_entry(pool_id, preserve_session=True)

        return pool

    async def flush_pool(self, pool_id: str, reason: str = "end") -> None:
        """Called by adapter on explicit disconnect. Awaits flush directly."""
        with self._lock:
            pool = self._pools.pop(pool_id, None)
        if pool is None:
            return
        agent = self._hub.agent_registry.get(pool.agent_name)
        if agent is not None and pool.user_id and hasattr(agent, "flush_session"):
            await agent.flush_session(pool, reason)

    def set_debounce_ms(self, ms: int) -> None:
        """Update debounce window on all live pools and future pools."""
        self._hub._debounce_ms = ms
        self._pool_config = replace(self._pool_config, debounce_ms=ms)
        with self._lock:
            for pool in self._pools.values():
                pool.debounce_ms = ms

    def set_cancel_on_new_message(self, enabled: bool) -> None:
        """Toggle cancel-in-flight on all live pools and future pools."""
        self._hub._cancel_on_new_message = enabled
        self._pool_config = replace(self._pool_config, cancel_on_new_message=enabled)
        with self._lock:
            for pool in self._pools.values():
                pool.cancel_on_new_message = enabled
