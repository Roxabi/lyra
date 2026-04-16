"""Pool delegation mixin for Hub — split from hub.py (#760).

Provides get_or_create_pool(), flush_pool(), set_debounce_ms(), and
set_cancel_on_new_message() for Hub.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..pool import Pool
    from .pool_manager import PoolManager


class HubPoolDelegationMixin:
    """Mixin providing pool management delegation for Hub."""

    # Declared for type-checking — initialised by Hub.__init__.
    if TYPE_CHECKING:
        _pool_manager: PoolManager

    def get_or_create_pool(self, pool_id: str, agent_name: str) -> Pool:
        return self._pool_manager.get_or_create_pool(pool_id, agent_name)

    async def flush_pool(self, pool_id: str, reason: str = "end") -> None:
        await self._pool_manager.flush_pool(pool_id, reason)

    def set_debounce_ms(self, ms: int) -> None:
        self._pool_manager.set_debounce_ms(ms)

    def set_cancel_on_new_message(self, enabled: bool) -> None:
        self._pool_manager.set_cancel_on_new_message(enabled)
