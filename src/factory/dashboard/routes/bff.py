"""Control-plane BFF axis routes — /api/bff/*."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from factory.dashboard.auth import require_principal
from factory.dashboard.e2e import (
    e2e_enabled,
    stub_agents_status,
    stub_fleet,
    stub_ops_health,
    stub_ops_logs,
    stub_pipeline,
    stub_resume,
    stub_sessions_list,
    stub_sessions_turns,
)
from factory.dashboard.ops_proxy import fetch_ops_health, fetch_ops_logs
from factory.dashboard.otel_client import fetch_spans
from factory.dashboard.pipeline_stream import (
    PIPELINE_STREAM_ID,
    pipeline_sse_events,
)
from factory.dashboard.routes.auth_routes import register_auth_routes
from factory.dashboard.routes.bff_admin import register_admin_routes
from factory.dashboard.routes.bff_agents import register_agent_routes
from factory.dashboard.routes.bff_common import map_hub_errors
from factory.dashboard.routes.bff_jobs import register_jobs_routes
from factory.dashboard.routes.link_routes import register_link_routes
from factory.dashboard.routes.org_routes import register_org_routes
from factory.dashboard.stream_tokens import StreamTokenRegistry
from roxabi_contracts.dashboard import (
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


def build_bff_router(  # noqa: C901, PLR0915
    adapter: WebAdapter,
    hub: DashboardHubClient,
    tokens: StreamTokenRegistry,
) -> APIRouter:
    """Build BFF router.

    Public: auth login/accept-invite (registered without principal deps).
    Protected: everything else — router-level ``require_principal`` so hub
    RPC is always stamped and local proxies are not anonymous.
    """
    router = APIRouter(prefix="/api/bff")
    protected = APIRouter(dependencies=[Depends(require_principal)])

    @protected.get("/agents/status")
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
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    @protected.get("/sessions")
    async def list_sessions(
        agent: str = Query(...),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> DashboardSessionsListResponse:
        if e2e_enabled():
            return stub_sessions_list(agent)
        try:
            return await hub.list_sessions(agent, limit=limit)
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    register_jobs_routes(protected, adapter, hub, tokens)

    @protected.get("/sessions/turns")
    async def list_session_turns(
        session_id: str = Query(...),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> DashboardSessionsTurnsResponse:
        if e2e_enabled():
            return stub_sessions_turns(session_id)
        try:
            return await hub.list_turns(session_id, limit=limit)
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    @protected.get("/spans")
    async def list_spans(
        pool_id: str | None = Query(default=None),
        job_id: str | None = Query(default=None),
        component: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        try:
            return await fetch_spans(
                pool_id=pool_id,
                job_id=job_id,
                component=component,
                page=page,
                page_size=page_size,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @protected.get("/ops/health")
    async def ops_health() -> DashboardOpsHealthResponse:
        if e2e_enabled():
            return stub_ops_health()
        return await fetch_ops_health()

    @protected.get("/ops/logs")
    async def ops_logs(
        preset: OpsLogPreset = Query(default="hub-errors"),
        container: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> DashboardOpsLogsResponse:
        if e2e_enabled():
            return stub_ops_logs(preset, container=container)
        try:
            return await fetch_ops_logs(
                preset,
                container=container,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    register_agent_routes(protected, hub)
    register_admin_routes(protected, hub)

    @protected.get("/fleet")
    async def fleet_list() -> dict:
        if e2e_enabled():
            return stub_fleet().model_dump()
        try:
            return (await hub.fleet_list()).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    @protected.get("/pipeline")
    async def pipeline_list() -> dict:
        if e2e_enabled():
            return stub_pipeline().model_dump()
        try:
            return (await hub.pipeline_list()).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    @protected.post("/pipeline/stream-token")
    async def pipeline_stream_token() -> dict[str, str]:
        stream_token = tokens.mint(PIPELINE_STREAM_ID)
        return {"stream_token": stream_token}

    @protected.get("/pipeline/stream")
    async def pipeline_stream(
        request: Request,
        token: str | None = Query(default=None),
    ) -> StreamingResponse:
        if not tokens.verify(PIPELINE_STREAM_ID, token):
            raise HTTPException(status_code=403, detail="invalid stream token")

        async def _client_connected() -> bool:
            return not await request.is_disconnected()

        async def event_gen():
            try:
                async for frame in pipeline_sse_events(
                    hub,
                    is_connected=_client_connected,
                ):
                    yield frame
            except asyncio.CancelledError:
                return
            finally:
                tokens.revoke(PIPELINE_STREAM_ID)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @protected.post("/sessions/resume")
    async def resume_session(
        body: DashboardSessionsResumeRequest,
    ) -> DashboardSessionsResumeResponse:
        if body.agent not in adapter.agent_names:
            raise HTTPException(
                status_code=400, detail=f"unknown agent: {body.agent!r}"
            )
        if e2e_enabled():
            return stub_resume()
        try:
            return await hub.resume_session(body.agent, body.cli_session_id)
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    # Auth public endpoints (login/accept-invite) + protected me/invite/keys.
    register_auth_routes(router)
    register_org_routes(router)
    register_link_routes(router)
    router.include_router(protected)
    return router
