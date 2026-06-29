"""Hub-side NATS RPC handlers for dashboard jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from factory.core.hub.hub_protocol import RoutingKey
from factory.core.hub.job_catalog import list_active_jobs
from factory.core.messaging.message import Platform
from factory.core.prompt_resolution import resolve_agent_runtime_defaults
from roxabi_contracts.dashboard import (
    DashboardJob,
    DashboardJobsLaunchRequest,
    DashboardJobsLaunchResponse,
    DashboardJobsListResponse,
    DashboardJobsSteerRequest,
    DashboardJobsSteerResponse,
)
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.jobs import JobEnvelope
from roxabi_contracts.jobs.subjects import jobs_steer, jobs_submit

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

_WEB_BOT = "smoke"
_ALLOWED_JOB_NAMES = frozenset({"claude", "omp", "test"})
_DEFAULT_OMP_MODEL = "grok-4-fast"


async def handle_jobs_list(hub: Hub, _nc: NATS, _payload: dict[str, Any]) -> dict:
    rows = await list_active_jobs(hub)
    jobs = [DashboardJob.model_validate(row) for row in rows]
    return DashboardJobsListResponse(jobs=jobs).model_dump()


async def handle_jobs_launch(hub: Hub, nc: NATS, payload: dict[str, Any]) -> dict:
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
    agent = hub.agent_registry.get(req.agent)
    store = getattr(hub, "_agent_store", None)
    row = store.get(req.agent) if store is not None else None
    backend = row.backend if row is not None else "claude-cli"
    model = row.model if row is not None else "sonnet"
    defaults = resolve_agent_runtime_defaults(backend=backend, model=model)
    if req.job_name == "claude":
        model_cfg = {
            "backend": defaults["backend"],
            "model": req.model or defaults["model"],
        }
    else:
        model_cfg = {
            "backend": "omp-rpc",
            "model": req.model or defaults["model"] or _DEFAULT_OMP_MODEL,
        }
    system_prompt = agent.config.system_prompt if agent is not None else ""
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
            "system_prompt": system_prompt,
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


async def handle_jobs_steer(_hub: Hub, nc: NATS, payload: dict[str, Any]) -> dict:
    req = DashboardJobsSteerRequest.model_validate(payload)
    subject = jobs_steer(req.job_id)
    await nc.publish(subject, req.text.encode())
    return DashboardJobsSteerResponse(
        accepted=True,
        message=f"steer published to {req.job_id}",
    ).model_dump()