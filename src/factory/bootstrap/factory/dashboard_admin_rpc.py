"""Hub-side NATS RPC handlers for dashboard admin views."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from factory.core.auth.agent_grants import AuthDecision, Capability, Principal, PrincipalKind
from roxabi_contracts.dashboard import (
    DashboardAdminAccessResponse,
    DashboardAdminPlatformIdentity,
    DashboardAdminUserAccess,
    DashboardAdminUserCreateRequest,
    DashboardAdminUserPatchRequest,
    DashboardAdminUserResponse,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.auth.user_models import User
    from factory.core.hub import Hub
    from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore
    from factory.infrastructure.stores.identity.user_store import UserStore

log = logging.getLogger(__name__)


def _user_store(hub: Hub) -> UserStore | None:
    return getattr(hub, "_user_store", None)


def _grant_store(hub: Hub) -> AgentGrantStore | None:
    authorizer = getattr(hub, "_authorizer", None)
    if authorizer is None or not hasattr(authorizer, "authorize"):
        return None
    return authorizer  # type: ignore[return-value]


def _agent_store(hub: Hub):
    store = getattr(hub, "_agent_store", None)
    if store is None:
        agent = next(iter(hub.agent_registry.values()), None)
        store = getattr(agent, "_agent_store", None) if agent else None
    return store


def _platform_identity(
    identities: tuple[Any, ...], platform: str
) -> DashboardAdminPlatformIdentity | None:
    for ident in identities:
        if ident.platform == platform:
            return DashboardAdminPlatformIdentity(
                platform=ident.platform,
                platform_uid=ident.platform_uid,
                platform_key=ident.platform_key,
            )
    return None


async def _agents_for_user(
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


async def _user_access_row(
    user: User,
    *,
    user_store: UserStore,
    grant_store: AgentGrantStore,
    agent_store: Any,
) -> DashboardAdminUserAccess:
    identities = await user_store.list_platform_identities(user.id)
    agents = await _agents_for_user(grant_store, agent_store, user.id)
    return DashboardAdminUserAccess(
        user_id=user.id,
        display_name=user.display_name,
        email=user.email,
        telegram=_platform_identity(identities, "telegram"),
        discord=_platform_identity(identities, "discord"),
        agents=agents,
    )


async def _sync_user_agents(
    grant_store: AgentGrantStore,
    agent_store: Any,
    user_id: str,
    desired_agents: list[str],
) -> None:
    known = {r.name for r in agent_store.get_all()}
    unknown = sorted(name for name in desired_agents if name not in known)
    if unknown:
        raise ValueError(f"unknown agent(s): {', '.join(unknown)}")

    current = set(await _agents_for_user(grant_store, agent_store, user_id))
    desired = set(desired_agents)
    principal = Principal(kind=PrincipalKind.USER, id=user_id)

    for agent_name in sorted(desired - current):
        await grant_store.grant(
            agent_name,
            principal,
            capability=Capability.USE,
            granted_by="dashboard",
            source="admin.user.patch",
        )

    for agent_name in sorted(current - desired):
        await grant_store.revoke(agent_name, principal, capability=Capability.USE)


async def _apply_platform_identities(
    user_store: UserStore,
    user_id: str,
    *,
    telegram_uid: str | None = None,
    discord_uid: str | None = None,
    apply_telegram: bool = False,
    apply_discord: bool = False,
) -> None:
    if apply_telegram:
        await user_store.set_platform_identity(user_id, "telegram", telegram_uid)
    if apply_discord:
        await user_store.set_platform_identity(user_id, "discord", discord_uid)


async def _user_response_row(
    user: User,
    *,
    user_store: UserStore,
    grant_store: AgentGrantStore,
    agent_store: Any,
) -> DashboardAdminUserResponse:
    row = await _user_access_row(
        user,
        user_store=user_store,
        grant_store=grant_store,
        agent_store=agent_store,
    )
    return DashboardAdminUserResponse.model_validate(row.model_dump())


async def handle_admin_access(hub: Hub, _nc: NATS, _payload: dict[str, Any]) -> dict:
    user_store = _user_store(hub)
    grant_store = _grant_store(hub)
    agent_store = _agent_store(hub)
    if user_store is None or grant_store is None or agent_store is None:
        return DashboardAdminAccessResponse(users=[]).model_dump()

    users_out: list[DashboardAdminUserAccess] = []
    for user in await user_store.list_users():
        users_out.append(
            await _user_access_row(
                user,
                user_store=user_store,
                grant_store=grant_store,
                agent_store=agent_store,
            )
        )

    return DashboardAdminAccessResponse(users=users_out).model_dump()


async def handle_admin_user_create(hub: Hub, _nc: NATS, payload: dict[str, Any]) -> dict:
    req = DashboardAdminUserCreateRequest.model_validate(payload.get("body") or payload)
    user_store = _user_store(hub)
    grant_store = _grant_store(hub)
    agent_store = _agent_store(hub)
    if user_store is None:
        return {"error": "store_unavailable"}
    try:
        user = await user_store.create_profile_user(
            display_name=req.display_name,
            email=req.email,
        )
        await _apply_platform_identities(
            user_store,
            user.id,
            telegram_uid=req.telegram_uid,
            discord_uid=req.discord_uid,
            apply_telegram=req.telegram_uid is not None,
            apply_discord=req.discord_uid is not None,
        )
        if grant_store is not None and agent_store is not None:
            await _sync_user_agents(grant_store, agent_store, user.id, req.agents)
    except ValueError as exc:
        return {"error": "conflict", "message": str(exc)}

    if grant_store is None or agent_store is None:
        return DashboardAdminUserResponse(
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
        ).model_dump()

    return (
        await _user_response_row(
            user,
            user_store=user_store,
            grant_store=grant_store,
            agent_store=agent_store,
        )
    ).model_dump()


async def handle_admin_user_patch(hub: Hub, _nc: NATS, payload: dict[str, Any]) -> dict:
    user_id = str(payload.get("user_id") or "")
    req = DashboardAdminUserPatchRequest.model_validate(payload.get("patch") or payload)
    user_store = _user_store(hub)
    grant_store = _grant_store(hub)
    agent_store = _agent_store(hub)
    if user_store is None or not user_id:
        return {"error": "not_found"}
    fields_set = req.model_fields_set
    try:
        user = await user_store.update_profile_user(
            user_id,
            display_name=req.display_name,
            email=req.email,
        )
        if user is None:
            return {"error": "not_found"}
        await _apply_platform_identities(
            user_store,
            user_id,
            telegram_uid=req.telegram_uid,
            discord_uid=req.discord_uid,
            apply_telegram="telegram_uid" in fields_set,
            apply_discord="discord_uid" in fields_set,
        )
        if (
            grant_store is not None
            and agent_store is not None
            and "agents" in fields_set
            and req.agents is not None
        ):
            await _sync_user_agents(grant_store, agent_store, user_id, req.agents)
    except ValueError as exc:
        return {"error": "conflict", "message": str(exc)}

    if grant_store is None or agent_store is None:
        return DashboardAdminUserResponse(
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
        ).model_dump()

    return (
        await _user_response_row(
            user,
            user_store=user_store,
            grant_store=grant_store,
            agent_store=agent_store,
        )
    ).model_dump()