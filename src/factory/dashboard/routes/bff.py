"""Control-plane BFF axis routes — /api/bff/*."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from factory.dashboard.e2e import (
    e2e_enabled,
    stub_agents_status,
    stub_jobs_launch,
    stub_jobs_list,
    stub_jobs_steer,
    stub_ops_health,
    stub_ops_logs,
    stub_resume,
    stub_sessions_list,
    stub_sessions_turns,
)
from factory.dashboard.ops_proxy import fetch_ops_health, fetch_ops_logs
from roxabi_contracts.dashboard import (
    DashboardAgentPatchRequest,
    DashboardAgentSoulPreviewRequest,
    DashboardAgentSoulPutRequest,
    DashboardJobsLaunchRequest,
    DashboardJobsLaunchResponse,
    DashboardJobsListResponse,
    DashboardJobsSteerRequest,
    DashboardJobsSteerResponse,
    DashboardOpsHealthResponse,
    DashboardOpsLogsResponse,
    DashboardSessionsListResponse,
    DashboardSessionsResumeRequest,
    DashboardSessionsResumeResponse,
    DashboardSessionsTurnsResponse,
    OpsLogPreset,
)

if TYPE_CHECKING:
    from factory.adapters.web.web_adapter import WebAdapter
    from factory.dashboard.hub_client import DashboardHubClient


def _sessions_auth_required() -> bool:
    return os.environ.get("FACTORY_DASHBOARD_AUTH_REQUIRED", "").strip() in {
        "1",
        "true",
        "yes",
    }


def _hub_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


def build_bff_router(  # noqa: C901, PLR0915
    adapter: WebAdapter, hub: DashboardHubClient
) -> APIRouter:
    router = APIRouter(prefix="/api/bff")

    @router.get("/agents/status")
    async def agents_status(
        harness: str | None = Query(default=None),
        agent: str | None = Query(default=None),
    ) -> dict:
        agents = adapter.agent_names
        if e2e_enabled():
            return stub_agents_status(agents).model_dump()
        harness_by_agent: dict[str, str] | None = None
        if harness and agent and agent in agents:
            harness_by_agent = {agent: harness}
        try:
            return (
                await hub.agents_status(agents, harness_by_agent=harness_by_agent)
            ).model_dump()
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/sessions")
    async def list_sessions(
        agent: str = Query(...),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> DashboardSessionsListResponse:
        if _sessions_auth_required():
            raise HTTPException(
                status_code=403,
                detail="session list requires operator auth (#1992)",
            )
        if e2e_enabled():
            return stub_sessions_list(agent)
        try:
            return await hub.list_sessions(agent, limit=limit)
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/jobs")
    async def list_jobs() -> DashboardJobsListResponse:
        if e2e_enabled():
            return stub_jobs_list()
        try:
            return await hub.list_jobs()
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/jobs/launch")
    async def launch_job(
        body: DashboardJobsLaunchRequest,
    ) -> DashboardJobsLaunchResponse:
        if body.agent not in adapter.agent_names:
            raise HTTPException(
                status_code=400, detail=f"unknown agent: {body.agent!r}"
            )
        if e2e_enabled():
            return stub_jobs_launch(body.agent)
        try:
            return await hub.launch_job(
                agent=body.agent,
                prompt=body.prompt,
                job_name=body.job_name,
                model=body.model,
            )
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/jobs/steer")
    async def steer_job(
        body: DashboardJobsSteerRequest,
    ) -> DashboardJobsSteerResponse:
        if e2e_enabled():
            return stub_jobs_steer(body.job_id)
        try:
            return await hub.steer_job(body.job_id, body.text)
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/sessions/turns")
    async def list_session_turns(
        session_id: str = Query(...),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> DashboardSessionsTurnsResponse:
        if _sessions_auth_required():
            raise HTTPException(
                status_code=403,
                detail="session turns requires operator auth (#1992)",
            )
        if e2e_enabled():
            return stub_sessions_turns(session_id)
        try:
            return await hub.list_turns(session_id, limit=limit)
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/ops/health")
    async def ops_health() -> DashboardOpsHealthResponse:
        if e2e_enabled():
            return stub_ops_health()
        return await fetch_ops_health()

    @router.get("/ops/logs")
    async def ops_logs(
        preset: OpsLogPreset = Query(default="hub-errors"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> DashboardOpsLogsResponse:
        if e2e_enabled():
            return stub_ops_logs(preset)
        return await fetch_ops_logs(preset, limit=limit)

    @router.get("/agents")
    async def list_agents_config() -> dict:
        try:
            return (await hub.list_agent_configs()).model_dump()
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/agents/{name}")
    async def get_agent_config(name: str) -> dict:
        if name not in adapter.agent_names:
            raise HTTPException(status_code=404, detail=f"unknown agent: {name!r}")
        try:
            return (await hub.get_agent_config(name)).model_dump()
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.patch("/agents/{name}")
    async def patch_agent_config(name: str, body: DashboardAgentPatchRequest) -> dict:
        if name not in adapter.agent_names:
            raise HTTPException(status_code=404, detail=f"unknown agent: {name!r}")
        try:
            return (await hub.patch_agent_config(name, body)).model_dump()
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.put("/agents/{name}/soul")
    async def put_agent_soul(name: str, body: DashboardAgentSoulPutRequest) -> dict:
        if name not in adapter.agent_names:
            raise HTTPException(status_code=404, detail=f"unknown agent: {name!r}")
        try:
            return (await hub.put_agent_soul(name, body)).model_dump()
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/agents/{name}/soul")
    async def get_agent_soul(name: str) -> dict:
        if name not in adapter.agent_names:
            raise HTTPException(status_code=404, detail=f"unknown agent: {name!r}")
        try:
            return (await hub.get_agent_soul(name)).model_dump()
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/agents/{name}/soul/preview")
    async def preview_agent_soul(
        name: str, body: DashboardAgentSoulPreviewRequest
    ) -> dict:
        if name not in adapter.agent_names:
            raise HTTPException(status_code=404, detail=f"unknown agent: {name!r}")
        try:
            return (await hub.preview_agent_soul(name, body)).model_dump()
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/sessions/resume")
    async def resume_session(
        body: DashboardSessionsResumeRequest,
    ) -> DashboardSessionsResumeResponse:
        if _sessions_auth_required():
            raise HTTPException(
                status_code=403,
                detail="session resume requires operator auth (#1992)",
            )
        if body.agent not in adapter.agent_names:
            raise HTTPException(
                status_code=400, detail=f"unknown agent: {body.agent!r}"
            )
        if e2e_enabled():
            return stub_resume()
        try:
            return await hub.resume_session(body.agent, body.cli_session_id)
        except RuntimeError as exc:
            raise _hub_unavailable(exc) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router