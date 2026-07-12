"""Grant USE on an agent for all linked platform ids of a dash user (ADR-103 §10)."""

from __future__ import annotations

from typing import Any, Protocol

from factory.core.auth.agent_grants import Capability, Principal, PrincipalKind

__all__ = ["GrantWriter", "PlatformKeyResolver", "grant_human_platforms"]


class PlatformKeyResolver(Protocol):
    def resolve_platform_keys(self, user_id: str) -> frozenset[str]: ...


class GrantWriter(Protocol):
    async def grant(
        self,
        agent_name: str,
        principal: Principal,
        *,
        capability: Capability = Capability.USE,
        granted_by: str,
        source: str,
    ) -> Any:  # AgentGrant | None — store returns grant row
        ...


async def grant_human_platforms(  # noqa: PLR0913 — grant matrix args are all required
    *,
    grant_store: GrantWriter,
    key_resolver: PlatformKeyResolver,
    agent_name: str,
    dash_user_id: str,
    granted_by: str,
    source: str = "dashboard.grant_human",
) -> list[str]:
    """Issue USE grants to every platform key linked to *dash_user_id*.

    Returns the list of platform keys granted. Raises ValueError if none linked.
    """
    keys = sorted(key_resolver.resolve_platform_keys(dash_user_id))
    if not keys:
        raise ValueError(
            f"user {dash_user_id!r} has no linked platform identities"
        )
    granted: list[str] = []
    for key in keys:
        await grant_store.grant(
            agent_name,
            Principal(kind=PrincipalKind.USER, id=key),
            capability=Capability.USE,
            granted_by=granted_by,
            source=source,
        )
        granted.append(key)
    return granted
