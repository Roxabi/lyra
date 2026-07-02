"""Jobs BFF routes — /api/bff/jobs* (list, launch, steer, cancel, SSE stream)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from factory.dashboard.e2e import (
    e2e_enabled,
    stub_jobs_cancel,
    stub_jobs_launch,
    stub_jobs_list,
    stub_jobs_steer,
)
from factory.dashboard.jobs_stream import JOBS_STREAM_ID, jobs_sse_events
from factory.dashboard.routes.bff_common import map_hub_errors
from roxabi_contracts.dashboard import (
    DashboardJobsCancelRequest,
    DashboardJobsCancelResponse,
    DashboardJobsLaunchRequest,
    DashboardJobsLaunchResponse,
    DashboardJobsListResponse,
    DashboardJobsSteerRequest,
    DashboardJobsSteerResponse,
)

if TYPE_CHECKING:
    from factory.adapters.web.web_adapter import WebAdapter
    from factory.dashboard.hub_client import DashboardHubClient
    from factory.dashboard.stream_tokens import StreamTokenRegistry


def register_jobs_routes(  # noqa: C901, PLR0915
    router: APIRouter,
    adapter: WebAdapter,
    hub: DashboardHubClient,
    tokens: StreamTokenRegistry,
) -> None:
    """Register /api/bff/jobs* routes on *router* (see bff.build_bff_router)."""

    @router.get("/jobs")
    async def list_jobs() -> DashboardJobsListResponse:
        if e2e_enabled():
            return stub_jobs_list()
        try:
            return await hub.list_jobs()
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

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
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.post("/jobs/steer")
    async def steer_job(
        body: DashboardJobsSteerRequest,
    ) -> DashboardJobsSteerResponse:
        if e2e_enabled():
            return stub_jobs_steer(body.job_id)
        try:
            return await hub.steer_job(body.job_id, body.text)
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.post("/jobs/stream-token")
    async def jobs_stream_token() -> dict[str, str]:
        stream_token = tokens.mint(JOBS_STREAM_ID)
        return {"stream_token": stream_token}

    @router.get("/jobs/stream")
    async def jobs_stream(
        request: Request,
        token: str | None = Query(default=None),
    ) -> StreamingResponse:
        if not tokens.verify(JOBS_STREAM_ID, token):
            raise HTTPException(status_code=403, detail="invalid stream token")

        async def _client_connected() -> bool:
            return not await request.is_disconnected()

        async def event_gen():
            try:
                async for frame in jobs_sse_events(
                    hub,
                    is_connected=_client_connected,
                ):
                    yield frame
            except asyncio.CancelledError:
                return
            finally:
                tokens.revoke(JOBS_STREAM_ID)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @router.post("/jobs/cancel")
    async def cancel_job(
        body: DashboardJobsCancelRequest,
    ) -> DashboardJobsCancelResponse:
        if e2e_enabled():
            return stub_jobs_cancel(body.job_id)
        try:
            return await hub.cancel_job(body.job_id)
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise
