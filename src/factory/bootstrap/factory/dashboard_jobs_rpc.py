"""Hub-side NATS RPC handlers for dashboard jobs (ADR-103 principal + job meta)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from factory.core.auth.control_plane_authz import (
    ResourceRef,
    authorize,
    filter_jobs_visible,
)
from factory.core.auth.control_plane_wire import get_request_principal
from factory.core.hub.hub_protocol import RoutingKey
from factory.core.hub.job_catalog import list_active_jobs
from factory.core.messaging.message import Platform
from factory.core.ports.active_jobs import (
    ActiveJobEntry,
    RegistryConflictError,
    concurrency_mode_for_backend,
)
from factory.core.prompt_resolution import resolve_agent_runtime_defaults
from factory.core.trace import TraceContext
from factory.nats.envelope_fields import mint_work_envelope_fields
from factory.obs.hub_tracer import nats_client_span
from roxabi_contracts.dashboard import (
    DashboardJob,
    DashboardJobsCancelRequest,
    DashboardJobsCancelResponse,
    DashboardJobsLaunchRequest,
    DashboardJobsLaunchResponse,
    DashboardJobsListResponse,
    DashboardJobsSteerRequest,
    DashboardJobsSteerResponse,
)
from roxabi_contracts.jobs import JobEnvelope
from roxabi_contracts.jobs.subjects import (
    JOB_CANCEL_STEER_TOKEN,
    jobs_steer,
    jobs_submit,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

_WEB_BOT = "smoke"
_ALLOWED_JOB_NAMES = frozenset({"claude", "omp", "test"})
_DEFAULT_OMP_MODEL = "grok-4-fast"


def _control_plane(hub: Hub):
    return getattr(hub, "_control_plane", None)


def _require_principal():
    principal = get_request_principal()
    if principal is None:
        return None, {
            "error": "unauthorized",
            "message": "principal required",
        }
    return principal, None


async def handle_jobs_list(hub: Hub, _nc: NATS, _payload: dict[str, Any]) -> dict:
    principal, err = _require_principal()
    if err is not None:
        return err
    assert principal is not None
    rows = await list_active_jobs(hub)
    cp = _control_plane(hub)
    if cp is not None:
        meta = await cp.job_meta_map([str(r["job_id"]) for r in rows])
        rows = filter_jobs_visible(principal, rows, meta_by_job=meta)
    elif not principal.is_admin:
        rows = []
    jobs = [DashboardJob.model_validate(row) for row in rows]
    return DashboardJobsListResponse(jobs=jobs).model_dump()


async def handle_jobs_launch(hub: Hub, nc: NATS, payload: dict[str, Any]) -> dict:
    principal, err = _require_principal()
    if err is not None:
        return err
    assert principal is not None
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

    org_id = principal.active_org_id
    if org_id is not None and org_id not in principal.org_ids:
        return DashboardJobsLaunchResponse(
            accepted=False,
            message="active org not in memberships",
        ).model_dump()

    pool_id = (
        req.pool_id
        or RoutingKey(Platform.WEB, _WEB_BOT, f"agent:{req.agent}").to_pool_id()
    )
    fields = mint_work_envelope_fields(
        trace_id=TraceContext.generate(),
        pool_id=pool_id,
    )
    job_id = fields.job_id
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
        contract_version=fields.contract_version,
        trace_id=fields.trace_id,
        issued_at=fields.issued_at,
        job_id=job_id,
        job_name=req.job_name,
        payload={
            "prompt": req.prompt,
            "pool_id": pool_id,
            "model_cfg": model_cfg,
            "system_prompt": system_prompt,
            "stream": True,
            "launched_by": principal.user_id,
            "org_id": org_id,
        },
        reply_to=f"_INBOX.{job_id}",
    )
    wire = envelope.model_dump_json().encode()
    with nats_client_span(
        name="dashboard-jobs",
        subject=dispatch_subject,
        payload=wire,
        trace_id=fields.trace_id,
        job_id=job_id,
        pool_id=pool_id,
    ):
        await nc.publish(dispatch_subject, wire)

    # Registry open at dispatch so Jobs view sees the run (#2142).
    # Key = envelope job_id (wire id) so ResultCloseListener can close it (#1795).
    await _open_dashboard_active_job(
        hub,
        job_id=job_id,
        pool_id=pool_id,
        backend=model_cfg.get("backend"),
    )

    cp = _control_plane(hub)
    if cp is not None:
        await cp.record_job_launch(job_id, launched_by=principal.user_id, org_id=org_id)

    return DashboardJobsLaunchResponse(
        accepted=True,
        job_id=job_id,
        message=f"dispatched {req.job_name}",
        dispatch_subject=dispatch_subject,
    ).model_dump()


async def _open_dashboard_active_job(
    hub: "Hub",
    *,
    job_id: str,
    pool_id: str,
    backend: str | None,
) -> None:
    """Best-effort registry open for a dashboard-dispatched job (#2142)."""
    coord = getattr(hub, "_active_jobs_coord", None)
    if coord is None or not hasattr(coord, "open"):
        return
    mode = concurrency_mode_for_backend(backend)
    entry = ActiveJobEntry(
        job_id=job_id,
        pool_id=pool_id,
        status="open",
        started_at=datetime.now(UTC),
        steer_subject=jobs_steer(job_id),
        concurrency_mode=mode,
    )
    try:
        await coord.open(entry)
    except RegistryConflictError:
        log.info(
            "dashboard jobs: pool %r already has an open job — "
            "launch %r not registered (TTL will heal)",
            pool_id,
            job_id,
        )
    except Exception:  # noqa: BLE001 — side-channel: never fail launch
        log.warning(
            "dashboard jobs: active-jobs open failed for %r",
            job_id,
            exc_info=True,
        )


async def _authorize_job_action(
    hub: Hub, principal, job_id: str, action: str
) -> dict | None:
    """Return error dict if denied, else None."""
    if principal.is_admin:
        return None
    cp = _control_plane(hub)
    launched_by, org_id = (None, None)
    if cp is not None:
        launched_by, org_id = await cp.get_job_meta(job_id)
    decision = authorize(
        principal,
        action,
        ResourceRef(kind="job", owner_user_id=launched_by, org_id=org_id),
    )
    if decision.allowed:
        return None
    return {
        "error": "forbidden",
        "message": f"not allowed to {action} job {job_id}",
    }


async def handle_jobs_steer(hub: Hub, nc: NATS, payload: dict[str, Any]) -> dict:
    principal, err = _require_principal()
    if err is not None:
        return err
    assert principal is not None
    req = DashboardJobsSteerRequest.model_validate(payload)
    denied = await _authorize_job_action(hub, principal, req.job_id, "jobs.steer")
    if denied is not None:
        resp = DashboardJobsSteerResponse(
            accepted=False, message=denied["message"]
        ).model_dump()
        return {**resp, "error": "forbidden"}
    subject = jobs_steer(req.job_id)
    await nc.publish(subject, req.text.encode())
    return DashboardJobsSteerResponse(
        accepted=True,
        message=f"steer published to {req.job_id}",
    ).model_dump()


async def handle_jobs_cancel(hub: Hub, nc: NATS, payload: dict[str, Any]) -> dict:
    principal, err = _require_principal()
    if err is not None:
        return err
    assert principal is not None
    req = DashboardJobsCancelRequest.model_validate(payload)
    denied = await _authorize_job_action(hub, principal, req.job_id, "jobs.cancel")
    if denied is not None:
        return {
            **DashboardJobsCancelResponse(
                accepted=False, message=denied["message"]
            ).model_dump(),
            "error": "forbidden",
        }
    job_id = req.job_id
    coord = getattr(hub, "_active_jobs_coord", None)
    if coord is not None and hasattr(coord, "close"):
        await coord.close(job_id)
    await nc.publish(jobs_steer(job_id), JOB_CANCEL_STEER_TOKEN.encode())
    return DashboardJobsCancelResponse(
        accepted=True,
        message=f"cancel published to {job_id}",
    ).model_dump()
