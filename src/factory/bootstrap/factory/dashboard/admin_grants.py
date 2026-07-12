"""Admin agent-grant sync helpers (ADR-103 Block 10 grant-human)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.core.auth.agent_grants import (
    AuthDecision,
    Capability,
    Principal,
    PrincipalKind,
)
from factory.core.auth.control_plane_wire import get_request_principal
from factory.core.auth.grant_human import grant_human_platforms

if TYPE_CHECKING:
    from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore
    from factory.infrastructure.stores.identity.user_store import UserStore

__all__ = [
    "agents_for_user",
    "sync_user_agents",
    "validate_desired_agents",
]


async def agents_for_user(
    grant_store: AgentGrantStore, agent_store: Any, user_id: str
) -> list[str]:
    agent_names = sorted(r.name for r in agent_store.get_all())
    agents: list[str] = []
    for agent_name in agent_names:
        decision: AuthDecision = grant_store.authorize(
            agent_name=agent_name,
            user_id=user_id,
        )
        if decision.allowed:
            agents.append(agent_name)
    return sorted(agents)


def validate_desired_agents(agent_store: Any, desired_agents: list[str]) -> str | None:
    known = {r.name for r in agent_store.get_all()}
    unknown = sorted(name for name in desired_agents if name not in known)
    if unknown:
        return f"unknown agent(s): {', '.join(unknown)}"
    return None


async def sync_user_agents(  # noqa: C901, PLR0913
    grant_store: AgentGrantStore,
    agent_store: Any,
    user_id: str,
    desired_agents: list[str],
    *,
    source: str = "admin.user.patch",
    granted_out: list[str] | None = None,
    granted_by: str | None = None,
    user_store: UserStore | None = None,
) -> list[str]:
    """Sync agent USE grants for a dash user onto all linked platform ids."""
    unknown_msg = validate_desired_agents(agent_store, desired_agents)
    if unknown_msg is not None:
        raise ValueError(unknown_msg)

    actor = granted_by
    if actor is None:
        p = get_request_principal()
        actor = p.user_id if p is not None else "dashboard"

    platform_keys: list[str] = []
    if user_store is not None:
        # Control-plane link/unlink may have mutated SQL outside UserStore.
        if hasattr(user_store, "rewarm_identity_cache"):
            await user_store.rewarm_identity_cache()
        platform_keys = sorted(user_store.resolve_platform_keys(user_id))

    current = set(await agents_for_user(grant_store, agent_store, user_id))
    if platform_keys:
        for key in platform_keys:
            current |= set(await agents_for_user(grant_store, agent_store, key))

    desired = set(desired_agents)
    granted = granted_out if granted_out is not None else []

    for agent_name in sorted(desired - current):
        if platform_keys and user_store is not None:
            await grant_human_platforms(
                grant_store=grant_store,
                key_resolver=user_store,
                agent_name=agent_name,
                dash_user_id=user_id,
                granted_by=actor,
                source=source,
            )
        else:
            await grant_store.grant(
                agent_name,
                Principal(kind=PrincipalKind.USER, id=user_id),
                capability=Capability.USE,
                granted_by=actor,
                source=source,
            )
        granted.append(agent_name)

    for agent_name in sorted(current - desired):
        subjects = platform_keys or [user_id]
        for sid in subjects:
            await grant_store.revoke(
                agent_name,
                Principal(kind=PrincipalKind.USER, id=sid),
                capability=Capability.USE,
            )

    return granted
