"""Shutdown mixin for Hub — split from hub.py (#760).

Provides notify_shutdown_inflight() and shutdown() for Hub.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.infrastructure.stores.turn_store import TurnStore

    from ..circuit_breaker import CircuitRegistry
    from ..memory import MemoryManager
    from ..messaging.message import Platform
    from ..messaging.messages import MessageManager
    from ..pool import Pool
    from ..stores.message_index import MessageIndex
    from .hub_protocol import ChannelAdapter
    from .outbound_dispatcher import OutboundDispatcher
    from .pool_manager import PoolManager

log = logging.getLogger(__name__)


class HubShutdownMixin:
    """Mixin providing graceful shutdown for Hub."""

    # Declared for type-checking — initialised by Hub.__init__.
    if TYPE_CHECKING:
        _pool_manager: PoolManager
        _memory_tasks: set[asyncio.Task]
        _memory: MemoryManager | None
        _turn_store: TurnStore | None
        _message_index: MessageIndex | None
        adapter_registry: dict[tuple[Platform, str], ChannelAdapter]
        outbound_dispatchers: dict[tuple[Platform, str], OutboundDispatcher]
        circuit_registry: CircuitRegistry | None
        _msg_manager: MessageManager | None

        @property
        def pools(self) -> dict[str, Pool]: ...

    async def notify_shutdown_inflight(self, active_pool_ids: list[str]) -> None:
        """Notify users of in-flight requests that are about to be killed.

        Called just before cli_pool.stop() during graceful shutdown.
        Fire-and-forget with a 3s total timeout so it never blocks teardown.
        """
        from ..messaging.message import Platform
        from .outbound_errors import try_notify_user

        _RESTART_MSG = (
            "\u26a0\ufe0f I was restarted mid-response"
            " \u2014 please resend your message."
        )
        _NOTIFY_TIMEOUT = 3.0

        async def _notify_one(pool_id: str) -> None:
            pool = self.pools.get(pool_id)
            if pool is None or pool._last_msg is None:
                return
            if pool.is_idle:
                # Subprocess alive but not processing — no in-flight turn to warn about.
                return
            msg = pool._last_msg
            platform_str = str(msg.platform)
            try:
                platform = Platform(platform_str)
            except ValueError:
                return
            adapter = self.adapter_registry.get((platform, msg.bot_id))
            if adapter is None:
                return
            circuit = (
                self.circuit_registry.get(platform_str)
                if self.circuit_registry is not None
                else None
            )
            await try_notify_user(
                platform_str, adapter, msg, _RESTART_MSG, circuit=circuit
            )

        tasks = [asyncio.create_task(_notify_one(pid)) for pid in active_pool_ids]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=_NOTIFY_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log.warning(
                    "notify_shutdown_inflight: timed out after %.1fs"
                    " (%d pools pending)",
                    _NOTIFY_TIMEOUT,
                    len(active_pool_ids),
                )

    async def shutdown(self) -> None:
        """Flush all live pools, drain pending memory tasks, close memory DB."""
        for pool_id in list(self._pool_manager.pools.keys()):
            await self._pool_manager.flush_pool(pool_id, "shutdown")
        if self._memory_tasks:
            await asyncio.gather(*self._memory_tasks, return_exceptions=True)
        if self._memory is not None:
            await self._memory.close()
        if self._turn_store is not None:
            await self._turn_store.close()
        if self._message_index is not None:
            await self._message_index.close()
