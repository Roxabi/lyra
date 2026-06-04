"""Hub-side LastSessionStore adapter: file-backed TurnStore wrapper (#1721).

``TurnStoreLastSession`` satisfies the ``LastSessionStore`` port for hub and
CLI pool processes that already mount ``turns.db`` via ``TurnStore``.

Design (D5):
- ``get_last_session`` delegates to ``TurnStore.get_last_session`` (file read).
- ``set_last_session`` is a deliberate **no-op**: turn_writer is the sole
  turns.db writer (ADR-075), so the hub must never write last-session via
  this path.  The NATS ``publish_start_session`` flow already maintains the
  turns.db record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.core.stores import TurnStoreProtocol


class TurnStoreLastSession:
    """``LastSessionStore`` wrapper backed by a ``TurnStore`` file.

    Intended for hub-side wiring (CLI pool, clipool, unified process) where
    ``turns.db`` is already mounted.  Adapter processes use
    ``KvLastSessionStore`` instead.

    ``set_last_session`` is a no-op: turn_writer is the sole turns.db writer
    (ADR-075).
    """

    def __init__(self, turn_store: "TurnStoreProtocol") -> None:
        self._ts = turn_store

    async def get_last_session(self, pool_id: str) -> str | None:
        """Delegate to ``TurnStore.get_last_session``."""
        return await self._ts.get_last_session(pool_id)

    async def set_last_session(self, pool_id: str, session_id: str) -> None:
        """No-op: turn_writer owns all writes to turns.db (ADR-075)."""
