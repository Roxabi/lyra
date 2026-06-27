"""Hub-side NATS RPC handlers for factory-dashboard BFF (#1771)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from nats.aio.msg import Msg
from pydantic import ValidationError

from factory.core.hub.hub_protocol import RoutingKey
from factory.core.hub.session_catalog import list_sessions_for_agent
from factory.core.messaging.message import Platform
from roxabi_contracts.dashboard import (
    SUBJECTS,
    AgentHealth,
    AgentHealthResponse,
    DashboardSession,
    DashboardSessionsListRequest,
    DashboardSessionsListResponse,
    DashboardSessionsResumeRequest,
    DashboardSessionsResumeResponse,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

log = logging.getLogger(__name__)

_CLIPOOL_WORKER = "clipool-worker"
_OMP_WORKER = "omp-worker"
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
        (SUBJECTS.agents_status, _handle_agents_status),
    ):
        sub = await nc.subscribe(subject, cb=_wrap(hub, handler))
        subs.append(sub)
        log.info("dashboard_rpc: subscribed %s", subject)
    return subs


def _wrap(hub: Hub, handler: Any):
    async def _cb(msg: Msg) -> None:
        try:
            payload = json.loads(msg.data.decode()) if msg.data else {}
            result = await handler(hub, payload)
            await msg.respond(json.dumps(result).encode())
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError, KeyError):
            log.exception("dashboard_rpc handler failed subject=%s", msg.subject)
            err = {"error": "bad_request"}
            await msg.respond(json.dumps(err).encode())
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch# hub RPC must always respond
            log.exception("dashboard_rpc handler failed subject=%s", msg.subject)
            err = {"error": "internal_error"}
            await msg.respond(json.dumps(err).encode())

    return _cb


async def _handle_sessions_list(hub: Hub, payload: dict[str, Any]) -> dict[str, Any]:
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


async def _handle_sessions_resume(hub: Hub, payload: dict[str, Any]) -> dict[str, Any]:
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


async def _handle_agents_status(hub: Hub, payload: dict[str, Any]) -> dict[str, Any]:
    agents: list[str] = list(payload.get("agents") or [])
    harness_by_agent: dict[str, str] = dict(payload.get("harness_by_agent") or {})
    freshness = getattr(hub, "_dashboard_worker_freshness", None)
    clipool_alive = _worker_alive(freshness, _CLIPOOL_WORKER)
    omp_alive = _worker_alive(freshness, _OMP_WORKER)
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


def _worker_alive(freshness: Any, worker_id: str) -> bool:
    if not isinstance(freshness, dict):
        return False
    import time

    ts = freshness.get(worker_id)
    if ts is None:
        return False
    return (time.monotonic() - ts) <= 30.0