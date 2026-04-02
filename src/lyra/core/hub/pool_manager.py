"""Pool lifecycle management: creation, eviction, flush."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ..pool import Pool

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)


class PoolManager:
    """Manages pool lifecycle: creation, stale eviction, explicit flush."""

    def __init__(self, hub: Hub) -> None:
        self._hub = hub
        self.pools: dict[str, Pool] = {}
        self._last_eviction_check: float = 0.0

    def get_or_create_pool(self, pool_id: str, agent_name: str) -> Pool:
        """Return existing pool or create a new one.

        Lazily evicts idle pools that have exceeded the TTL on each call
        to bound memory growth.
        """
        self._evict_stale_pools()
        if pool_id not in self.pools:
            new_pool = Pool(
                pool_id=pool_id,
                agent_name=agent_name,
                ctx=self._hub,
                debounce_ms=self._hub._debounce_ms,
                turn_timeout_ceiling=self._hub._turn_timeout,
                max_sdk_history=self._hub._max_sdk_history,
                safe_dispatch_timeout=self._hub._safe_dispatch_timeout,
                max_merged_chars=self._hub._max_merged_chars,
                cancel_on_new_message=self._hub._cancel_on_new_message,
            )
            if self._hub._turn_store is not None:
                new_pool._observer.register_turn_store(self._hub._turn_store)
            if self._hub._message_index is not None:
                new_pool._observer.register_message_index(self._hub._message_index)
            self.pools[pool_id] = new_pool
        pool = self.pools[pool_id]
        pool._touch()
        return pool

    def _evict_stale_pools(self) -> None:
        """Remove idle pools whose last activity exceeds the TTL.

        Throttled: skips the scan if less than TTL/10 has elapsed since the
        last check, turning the common case (nothing to evict) into a single
        float comparison.
        """
        now = time.monotonic()
        if (now - self._last_eviction_check) < self._hub._pool_ttl / 10:
            return
        self._last_eviction_check = now
        stale = [
            pid
            for pid, pool in self.pools.items()
            if pool.is_idle and (now - pool.last_active) > self._hub._pool_ttl
        ]
        for pid in stale:
            pool = self.pools.pop(pid)
            if pool.user_id:  # skip zero-message pools
                agent = self._hub.agent_registry.get(pool.agent_name)
                if agent is not None and hasattr(agent, "flush_session"):
                    task = asyncio.ensure_future(agent.flush_session(pool, "idle"))
                    self._hub._memory_tasks.add(task)

                    def _on_flush_done(
                        t: asyncio.Task, _pid: str = pid
                    ) -> None:
                        self._hub._memory_tasks.discard(t)
                        if not t.cancelled() and t.exception():
                            log.error(
                                "flush_session failed during eviction (pool=%s): %s",
                                _pid,
                                t.exception(),
                            )

                    task.add_done_callback(_on_flush_done)
            # Evict CLI entry synchronously so a new pool can't claim the old
            # process. preserve_session=True stores session_id for auto-resume
            # on next spawn (mirrors _kill contract; see #370).
            if self._hub.cli_pool is not None:
                self._hub.cli_pool._sync_evict_entry(pid, preserve_session=True)
        if stale:
            log.info("evicted %d stale pool(s)", len(stale))
            log.debug("evicted pool IDs: %s", stale)

    async def flush_pool(self, pool_id: str, reason: str = "end") -> None:
        """Called by adapter on explicit disconnect. Awaits flush directly."""
        pool = self.pools.pop(pool_id, None)
        if pool is None:
            return
        agent = self._hub.agent_registry.get(pool.agent_name)
        if agent is not None and pool.user_id and hasattr(agent, "flush_session"):
            await agent.flush_session(pool, reason)

    def set_debounce_ms(self, ms: int) -> None:
        """Update debounce window on all live pools and future pools."""
        self._hub._debounce_ms = ms
        for pool in self.pools.values():
            pool.debounce_ms = ms

    def set_cancel_on_new_message(self, enabled: bool) -> None:
        """Toggle cancel-in-flight on all live pools and future pools."""
        self._hub._cancel_on_new_message = enabled
        for pool in self.pools.values():
            pool.cancel_on_new_message = enabled
