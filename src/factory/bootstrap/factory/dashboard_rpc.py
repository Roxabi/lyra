"""Hub-side NATS RPC handlers for factory-dashboard BFF (#1771)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from nats.aio.msg import Msg

from factory.bootstrap.factory.dashboard.admin_rpc import (
    handle_admin_access,
    handle_admin_user_create,
    handle_admin_user_patch,
)
from factory.bootstrap.factory.dashboard.auth_rpc import (
    handle_auth_api_key_resolve,
    handle_auth_invite_accept,
    handle_auth_invite_create,
    handle_auth_login,
    handle_auth_logout,
    handle_auth_password_change,
    handle_auth_session_resolve,
)
from factory.bootstrap.factory.dashboard.connectors_rpc import (
    handle_connectors_delete,
    handle_connectors_list,
    handle_connectors_upsert,
)
from factory.bootstrap.factory.dashboard.fleet_rpc import handle_fleet_list
from factory.bootstrap.factory.dashboard.identity_rpc import (
    handle_identity_cache_rewarm,
)
from factory.bootstrap.factory.dashboard.pipeline_rpc import handle_pipeline_list
from factory.bootstrap.factory.dashboard.rpc_wrap import wrap_dashboard_handler
from factory.bootstrap.factory.dashboard.voice_rpc import handle_voice_capabilities
from factory.bootstrap.factory.dashboard_agents_rpc import (
    handle_agents_create,
    handle_agents_get,
    handle_agents_list,
    handle_agents_patch,
    handle_agents_soul_get,
    handle_agents_soul_preview,
    handle_agents_soul_put,
)
from factory.bootstrap.factory.dashboard_jobs_rpc import (
    handle_jobs_cancel,
    handle_jobs_launch,
    handle_jobs_list,
    handle_jobs_steer,
)
from factory.core.hub.hub_protocol import RoutingKey
from factory.core.hub.session_catalog import list_sessions_for_agent
from factory.core.messaging.message import Platform
from factory.dashboard.heartbeat import CLIPOOL_QUEUE, OMP_QUEUE, queue_group_alive
from roxabi_contracts.dashboard import (
    SUBJECTS,
    AgentHealth,
    AgentHealthResponse,
    DashboardSession,
    DashboardSessionsListRequest,
    DashboardSessionsListResponse,
    DashboardSessionsResumeRequest,
    DashboardSessionsResumeResponse,
    DashboardSessionsTurnsRequest,
    DashboardSessionsTurnsResponse,
    DashboardTurn,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

log = logging.getLogger(__name__)

_WEB_BOT = "smoke"


async def start_dashboard_rpc(hub: Hub, nc: NATS) -> list[Any]:
    """Subscribe hub responders for dashboard BFF request-reply."""
    import time

    freshness: dict[str, float] = {}
    hub._dashboard_worker_freshness = freshness  # noqa: SLF001

    async def _on_heartbeat(msg: Msg) -> None:
        try:
            data = json.loads(msg.data.decode())
            worker_id = str(data.get("worker_id") or data.get("id") or "")
            if worker_id:
                freshness[worker_id] = time.monotonic()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

    subs: list[Any] = []
    for hb_subject in ("factory.clipool.heartbeat", "factory.omp.heartbeat"):
        subs.append(await nc.subscribe(hb_subject, cb=_on_heartbeat))

    for subject, handler in (
        (SUBJECTS.sessions_list, _handle_sessions_list),
        (SUBJECTS.sessions_resume, _handle_sessions_resume),
        (SUBJECTS.sessions_turns, _handle_sessions_turns),
        (SUBJECTS.jobs_list, _wrap_agents(handle_jobs_list)),
        (SUBJECTS.jobs_launch, _wrap_agents(handle_jobs_launch)),
        (SUBJECTS.jobs_steer, _wrap_agents(handle_jobs_steer)),
        (SUBJECTS.jobs_cancel, _wrap_agents(handle_jobs_cancel)),
        (SUBJECTS.agents_status, _handle_agents_status),
        (SUBJECTS.agents_list, _wrap_agents(handle_agents_list)),
        (SUBJECTS.agents_create, _wrap_agents(handle_agents_create)),
        (SUBJECTS.agents_get, _wrap_agents(handle_agents_get)),
        (SUBJECTS.agents_patch, _wrap_agents(handle_agents_patch)),
        (SUBJECTS.admin_access, _wrap_agents(handle_admin_access)),
        (SUBJECTS.admin_user_create, _wrap_agents(handle_admin_user_create)),
        (SUBJECTS.admin_user_patch, _wrap_agents(handle_admin_user_patch)),
        (SUBJECTS.agents_soul_put, _wrap_agents(handle_agents_soul_put)),
        (SUBJECTS.agents_soul_get, _wrap_agents(handle_agents_soul_get)),
        (SUBJECTS.agents_soul_preview, _wrap_agents(handle_agents_soul_preview)),
        (SUBJECTS.voice_capabilities, handle_voice_capabilities),
        (SUBJECTS.fleet_list, handle_fleet_list),
        (SUBJECTS.pipeline_list, handle_pipeline_list),
        (SUBJECTS.connectors_installations_list, handle_connectors_list),
        (SUBJECTS.connectors_installations_upsert, handle_connectors_upsert),
        (SUBJECTS.connectors_installations_delete, handle_connectors_delete),
        (SUBJECTS.identity_cache_rewarm, handle_identity_cache_rewarm),
        (SUBJECTS.auth_login, handle_auth_login),
        (SUBJECTS.auth_logout, handle_auth_logout),
        (SUBJECTS.auth_session_resolve, handle_auth_session_resolve),
        (SUBJECTS.auth_invite_create, handle_auth_invite_create),
        (SUBJECTS.auth_invite_accept, handle_auth_invite_accept),
        (SUBJECTS.auth_api_key_resolve, handle_auth_api_key_resolve),
        (SUBJECTS.auth_password_change, handle_auth_password_change),
    ):
        sub = await nc.subscribe(subject, cb=wrap_dashboard_handler(hub, nc, handler))
        subs.append(sub)
        log.info("dashboard_rpc: subscribed %s", subject)
    return subs


def _wrap_agents(handler: Any):
    async def _inner(hub: Hub, nc: NATS, payload: dict[str, Any]) -> dict[str, Any]:
        return await handler(hub, nc, payload)

    return _inner


# Re-export for tests / legacy imports
_wrap = wrap_dashboard_handler


async def _handle_sessions_list(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = nc
    req = DashboardSessionsListRequest.model_validate(payload)
    store = hub._turn_store  # noqa: SLF001
    if store is None:
        return DashboardSessionsListResponse(sessions=[]).model_dump()
    rows = await list_sessions_for_agent(
        store, hub.bindings, req.agent, limit=req.limit
    )
    sessions = [
        DashboardSession(
            session_id=row["session_id"],
            pool_id=row["pool_id"],
            platform=row["platform"],  # type: ignore[arg-type]
            cli_session_id=row.get("cli_session_id"),
            first_user_msg=row.get("first_user_msg"),
            turn_count=int(row.get("turn_count") or 0),
            last_active_at=row["last_active_at"],
        )
        for row in rows
    ]
    return DashboardSessionsListResponse(sessions=sessions).model_dump()


async def _handle_sessions_turns(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = nc
    req = DashboardSessionsTurnsRequest.model_validate(payload)
    store = hub._turn_store  # noqa: SLF001
    if store is None:
        return DashboardSessionsTurnsResponse(turns=[]).model_dump()
    rows = await store.get_turns_by_session(req.session_id, limit=req.limit)
    turns = [
        DashboardTurn(
            role=row["role"],  # type: ignore[arg-type]
            content=row["content"],
            timestamp=row["timestamp"],
        )
        for row in rows
        if row["role"] in {"user", "assistant"}
    ]
    return DashboardSessionsTurnsResponse(turns=turns).model_dump()


async def _handle_sessions_resume(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = nc
    req = DashboardSessionsResumeRequest.model_validate(payload)
    pool_id = RoutingKey(Platform.WEB, _WEB_BOT, f"agent:{req.agent}").to_pool_id()
    pool = hub.pools.get(pool_id)
    if pool is None:
        return DashboardSessionsResumeResponse(
            accepted=False, message="no active web pool for agent"
        ).model_dump()
    if not pool.is_idle:
        return DashboardSessionsResumeResponse(
            accepted=False, message="turn in flight — wait or /stop"
        ).model_dump()
    accepted = await pool.resume_session(req.cli_session_id)
    if not accepted:
        return DashboardSessionsResumeResponse(
            accepted=False, message="resume refused by backend"
        ).model_dump()
    return DashboardSessionsResumeResponse(
        accepted=True, message=f"resumed {req.cli_session_id}"
    ).model_dump()


async def _handle_agents_status(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = nc
    agents: list[str] = list(payload.get("agents") or [])
    harness_by_agent: dict[str, str] = dict(payload.get("harness_by_agent") or {})
    freshness = getattr(hub, "_dashboard_worker_freshness", None)
    clipool_alive = queue_group_alive(freshness, CLIPOOL_QUEUE)
    omp_alive = queue_group_alive(freshness, OMP_QUEUE)
    roster = set(hub.agent_registry)
    result = []
    for name in agents:
        in_roster = name in roster
        harness = harness_by_agent.get(name, "claude-cli")
        harness_alive = omp_alive if harness == "omp-rpc" else clipool_alive
        result.append(
            AgentHealth(
                agent=name,
                in_roster=in_roster,
                harness=harness,  # type: ignore[arg-type]
                harness_reachable=harness_alive,
                online=in_roster and harness_alive,
            )
        )
    return AgentHealthResponse(agents=result).model_dump()
