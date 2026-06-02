"""AuthStoreProtocol — structural interface for authorization stores.

Decouples factory.core from the concrete SQLite AuthStore implementation
(factory.infrastructure.stores.auth_store). Any conforming implementation
(SQLite, in-memory, test double) can be wired in transparently.

Import only from factory.core — no infrastructure dependencies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from factory.core.auth.trust import TrustLevel

__all__ = ["AuthStoreProtocol"]


@runtime_checkable
class AuthStoreProtocol(Protocol):
    """Structural protocol for authorization grant stores.

    check() is synchronous and reads from an in-memory cache so it never
    blocks the event loop. Write operations (upsert, revoke) are async.
    """

    def check(self, identity_key: str) -> TrustLevel:
        """Return the TrustLevel for *identity_key* (sync, no I/O).

        Returns the store default (typically PUBLIC) when the key is unknown
        or its grant has expired.
        """
        ...

    async def upsert(
        self,
        identity_key: str,
        trust_level: TrustLevel,
        expires_at: datetime | None,
        granted_by: str,
        source: str,
    ) -> None:
        """Insert or replace a grant for *identity_key*."""
        ...

    async def revoke(self, identity_key: str) -> bool:
        """Delete the grant for *identity_key*.

        Returns True if a grant existed and was removed, False otherwise.
        """
        ...
