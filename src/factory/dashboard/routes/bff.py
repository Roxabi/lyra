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
    stub_resume,
    stub_sessions_list,
)
from roxabi_contracts.dashboard import (
    DashboardSessionsListResponse,
    DashboardSessionsResumeRequest,
    DashboardSessionsResumeResponse,
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


def build_bff_router(  # noqa: C901
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