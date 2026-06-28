"""Hub-side NATS RPC handlers for factory-dashboard BFF (#1771)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from nats.aio.msg import Msg
from pydantic import ValidationError

from factory.core.hub.hub_protocol import RoutingKey
from factory.core.hub.job_catalog import list_active_jobs
from factory.core.hub.session_catalog import list_sessions_for_agent
from factory.core.messaging.message import Platform
from roxabi_contracts.dashboard import (
    SUBJECTS,
    AgentHealth,
    AgentHealthResponse,
    DashboardJob,
    DashboardJobsLaunchRequest,
    DashboardJobsLaunchResponse,
    DashboardJobsListResponse,
    DashboardJobsSteerRequest,
    DashboardJobsSteerResponse,
    DashboardSession,
    DashboardSessionsListRequest,
    DashboardSessionsListResponse,
    DashboardSessionsResumeRequest,
    DashboardSessionsResumeResponse,
    DashboardSessionsTurnsRequest,
    DashboardSessionsTurnsResponse,
    DashboardTurn,
    DashboardVoiceCapabilitiesResponse,
    VoiceEngineInfo,
    VoiceSampleInfo,
    VoiceSttCapabilities,
    VoiceTtsCapabilities,
)
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.jobs import JobEnvelope
from roxabi_contracts.jobs.subjects import jobs_steer, jobs_submit

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

log = logging.getLogger(__name__)

_CLIPOOL_WORKER = "clipool-worker"
_OMP_WORKER = "omp-worker"
_WEB_BOT = "smoke"
_ALLOWED_JOB_NAMES = frozenset({"claude", "omp", "test"})
_DEFAULT_OMP_MODEL = "grok-4-fast"


async def start_dashboard_rpc(hub: Hub, nc: NATS) -> list[Any]:
    """Subscribe hub responders for dashboard BFF request-reply."""
    import time

    freshness: dict[str, float] = {}
    hub._dashboard_worker_freshness = freshness  # noqa: SLF001
    hub._dashboard_nc = nc  # noqa: SLF001

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
        (SUBJECTS.jobs_list, _handle_jobs_list),
        (SUBJECTS.jobs_launch, _handle_jobs_launch),
        (SUBJECTS.jobs_steer, _handle_jobs_steer),
        (SUBJECTS.agents_status, _handle_agents_status),
        (SUBJECTS.voice_capabilities, _handle_voice_capabilities),
    ):
        sub = await nc.subscribe(subject, cb=_wrap(hub, nc, handler))
        subs.append(sub)
        log.info("dashboard_rpc: subscribed %s", subject)
    return subs


def _wrap(hub: Hub, nc: NATS, handler: Any):
    async def _cb(msg: Msg) -> None:
        try:
            payload = json.loads(msg.data.decode()) if msg.data else {}
            result = await handler(hub, nc, payload)
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


async def _handle_jobs_list(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = nc
    _ = payload
    rows = await list_active_jobs(hub)
    jobs = [DashboardJob.model_validate(row) for row in rows]
    return DashboardJobsListResponse(jobs=jobs).model_dump()


async def _handle_jobs_launch(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    req = DashboardJobsLaunchRequest.model_validate(payload)
    if req.agent not in hub.agent_registry:
        return DashboardJobsLaunchResponse(
            accepted=False,
            message=f"unknown agent: {req.agent!r}",
        ).model_dump()
    if req.job_name not in _ALLOWED_JOB_NAMES:
        return DashboardJobsLaunchResponse(
            accepted=False,
            message=f"job_name not allowed: {req.job_name!r}",
        ).model_dump()

    job_id = uuid4().hex
    pool_id = req.pool_id or RoutingKey(
        Platform.WEB, _WEB_BOT, f"agent:{req.agent}"
    ).to_pool_id()
    dispatch_subject = jobs_submit(req.job_name)
    if req.job_name == "claude":
        model_cfg = {
            "backend": "claude-cli",
            "model": req.model or "sonnet",
        }
    else:
        model_cfg = {
            "backend": "omp-rpc",
            "model": req.model or _DEFAULT_OMP_MODEL,
        }
    envelope = JobEnvelope(
        contract_version=CONTRACT_VERSION,
        trace_id=job_id,
        issued_at=datetime.now(tz=UTC),
        job_id=job_id,
        job_name=req.job_name,
        payload={
            "prompt": req.prompt,
            "pool_id": pool_id,
            "model_cfg": model_cfg,
            "system_prompt": req.system_prompt,
            "stream": True,
        },
        reply_to=f"_INBOX.{job_id}",
    )
    await nc.publish(dispatch_subject, envelope.model_dump_json().encode())
    return DashboardJobsLaunchResponse(
        accepted=True,
        job_id=job_id,
        message=f"dispatched {req.job_name}",
        dispatch_subject=dispatch_subject,
    ).model_dump()


async def _handle_jobs_steer(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = hub
    req = DashboardJobsSteerRequest.model_validate(payload)
    subject = jobs_steer(req.job_id)
    await nc.publish(subject, req.text.encode())
    return DashboardJobsSteerResponse(
        accepted=True,
        message=f"steer published to {req.job_id}",
    ).model_dump()


async def _handle_agents_status(
    hub: Hub, nc: NATS, payload: dict[str, Any]
) -> dict[str, Any]:
    _ = nc
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


async def _handle_voice_capabilities(
    hub: Hub, nc: NATS, _payload: dict[str, Any]
) -> dict[str, Any]:
    _ = hub
    if nc is None:
        return DashboardVoiceCapabilitiesResponse(error="nats_unavailable").model_dump()
    from factory.nats.voice.voice_lifecycle_client import VoiceLifecycleClient

    caps = await VoiceLifecycleClient(nc).capabilities()
    tts_raw = caps.get("tts")
    stt_raw = caps.get("stt")
    tts = None
    stt = None
    if isinstance(tts_raw, dict):
        tts = VoiceTtsCapabilities(
            engines=[
                VoiceEngineInfo.model_validate(e) for e in tts_raw.get("engines", [])
            ],
            samples=[
                VoiceSampleInfo.model_validate(s) for s in tts_raw.get("samples", [])
            ],
            max_cached_engines=int(tts_raw.get("max_cached_engines") or 1),
            default_engine=tts_raw.get("default_engine"),
            catalog_revision=tts_raw.get("catalog_revision"),
        )
    if isinstance(stt_raw, dict):
        stt = VoiceSttCapabilities(
            models=list(stt_raw.get("models") or []),
            default_model=stt_raw.get("default_model"),
        )
    if tts is None and stt is None:
        return DashboardVoiceCapabilitiesResponse(
            error="voice_workers_unreachable",
        ).model_dump()
    return DashboardVoiceCapabilitiesResponse(tts=tts, stt=stt).model_dump()


def _worker_alive(freshness: Any, worker_id: str) -> bool:
    if not isinstance(freshness, dict):
        return False
    import time

    ts = freshness.get(worker_id)
    if ts is None:
        return False
    return (time.monotonic() - ts) <= 30.0