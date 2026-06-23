"""AgentGrantStoreProtocol — structural interface for the agent-grant store.

Application-layer code (the operator CLI of ADR-090 §7, bootstrap wiring) depends
on this protocol rather than the concrete SQLite ``AgentGrantStore``
(``factory.infrastructure.stores.identity.agent_grant_store``), following the
dependency-inversion rule of ADR-059.

It extends the narrow read port ``AgentAuthorizer`` (ADR-090 §4, owned by the
auth domain) with the operator write surface (``grant`` / ``revoke``) and the
synchronous matrix view used by ``factory agent auth list``.

Import only from factory.core — no infrastructure dependencies.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from factory.core.auth.agent_grants import (
    AgentAuthorizer,
    AgentGrant,
    Capability,
    Principal,
)

__all__ = ["AgentGrantStoreProtocol"]


@runtime_checkable
class AgentGrantStoreProtocol(AgentAuthorizer, Protocol):
    """Full agent-grant store contract: read authorization + operator writes.

    ``authorize`` (inherited) and ``list_grants`` are synchronous cache reads;
    ``grant`` and ``revoke`` are async DB writes that update the cache
    write-through, so grants take effect without a hub restart (ADR-090 §4).
    """

    def list_grants(self, agent_name: str) -> tuple[AgentGrant, ...]:
        """Return all grants for *agent_name* from cache (sync, no I/O)."""
        ...

    async def grant(
        self,
        agent_name: str,
        principal: Principal,
        *,
        capability: Capability = Capability.USE,
        granted_by: str,
        source: str,
    ) -> AgentGrant:
        """Insert or update a grant, returning the persisted row."""
        ...

    async def revoke(
        self,
        agent_name: str,
        principal: Principal,
        *,
        capability: Capability = Capability.USE,
    ) -> bool:
        """Delete a grant, returning True if one existed."""
        ...
