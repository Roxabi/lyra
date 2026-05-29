"""TurnStoreProtocol — structural interface for raw turn stores.

Decouples lyra.core from the concrete SQLite TurnStore implementation
(lyra.infrastructure.stores.turn_store). Fixes the layering inversion where
core modules imported an infrastructure type directly (issue #1079).

Factory: obtain a store via the bootstrap layer; type-annotate against this
protocol so that any conforming implementation (SQLite, in-memory, NATS-backed)
can be wired in transparently.
"""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable

__all__ = ["SessionRow", "TurnRow", "TurnStoreProtocol"]


class TurnRow(TypedDict):
    """One row from the ``conversation_turns`` table."""

    id: int
    pool_id: str
    session_id: str
    role: str
    platform: str
    user_id: str
    content: str
    message_id: str | None
    reply_message_id: str | None
    timestamp: str
    metadata: dict


class SessionRow(TypedDict):
    """One row from ``list_sessions`` — session summary with first message."""

    session_id: str
    cli_session_id: str | None
    last_active_at: str
    first_user_msg: str | None
    turn_count: int


@runtime_checkable
class TurnStoreProtocol(Protocol):
    """Read-path structural protocol for TurnStore consumers in lyra.core.

    Covers the public interface used by core/ modules (session_lifecycle,
    session_commands, pool, pool_observer).  Write-path methods (_log_turn,
    _start_session, etc.) are internal to the TurnWriter subscriber and are
    intentionally excluded — they are never called by core/ consumers directly.
    """

    async def get_turns(
        self, pool_id: str, user_id: str, limit: int = 50
    ) -> list[TurnRow]: ...

    async def list_sessions(self, pool_id: str, limit: int = 5) -> list[SessionRow]: ...

    async def get_cli_session(self, session_id: str) -> str | None: ...

    async def get_cli_session_by_pool(self, pool_id: str) -> str | None: ...

    async def get_last_session(self, pool_id: str) -> str | None: ...

    async def get_session_pool_id(self, session_id: str) -> str | None: ...
