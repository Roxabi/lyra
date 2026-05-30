"""MessageIndexProtocol — structural interface for the message-to-session index.

Decouples lyra.core from the concrete SQLite MessageIndex implementation
(lyra.infrastructure.stores.message_index). Fixes the layering inversion where
core modules imported an infrastructure type directly (issue #1529).

Factory: obtain a store via the bootstrap layer; type-annotate against this
protocol so that any conforming implementation (SQLite, in-memory, …) can be
wired in transparently.

Lifecycle methods (connect/close) and cleanup_older_than are intentionally
excluded — not core's concern; lifecycle is owned by bootstrap_stores.open_stores()
per ADR-078.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

__all__ = ["MessageIndexProtocol"]


@runtime_checkable
class MessageIndexProtocol(Protocol):
    """Read/write structural protocol for MessageIndex consumers in lyra.core.

    Covers the public interface used by core/ modules (hub_registration,
    pool_observer). Lifecycle methods (connect/close) and cleanup_older_than
    are intentionally excluded — lifecycle is owned by bootstrap_stores.open_stores()
    (ADR-078); the hub is a write-path consumer, not the owner.
    """

    async def resolve(self, pool_id: str, platform_msg_id: str) -> str | None: ...

    async def upsert(
        self,
        pool_id: str,
        platform_msg_id: str | None,
        session_id: str,
        role: Literal["user", "assistant"],
    ) -> None: ...
