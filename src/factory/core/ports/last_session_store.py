"""Driven port: last-session lookup/write for pool-to-session resume (#1721).

``LastSessionStore`` is a structural Protocol — any object with
``get_last_session`` / ``set_last_session`` satisfies it without explicit
inheritance.  Adapters inject a ``KvLastSessionStore`` (NATS KV-backed);
the hub injects a ``TurnStoreLastSession`` wrapper (file-backed, set=no-op).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LastSessionStore(Protocol):
    """Driven port: read/write the most-recent session_id for a pool.

    Implementations
    ---------------
    ``KvLastSessionStore`` — NATS KV bucket ``factory-turns-meta``; used by
    adapter processes (Telegram, Discord).

    ``TurnStoreLastSession`` — delegates ``get`` to ``TurnStore.get_last_session``;
    ``set`` is a deliberate no-op (hub: turn_writer is the sole turns.db writer,
    ADR-075).
    """

    async def get_last_session(self, pool_id: str) -> str | None:
        """Return the most-recent session_id for *pool_id*, or ``None`` on miss."""
        ...

    async def set_last_session(self, pool_id: str, session_id: str) -> None:
        """Persist *session_id* as the most-recent session for *pool_id*."""
        ...
