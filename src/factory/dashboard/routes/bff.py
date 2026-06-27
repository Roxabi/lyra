"""Control-plane BFF axis routes — Block 1 slice (/api/bff/agents/status only)."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError
from roxabi_contracts.dashboard import AgentHealth, AgentHealthResponse

if TYPE_CHECKING:
    from factory.adapters.web.web_adapter import WebAdapter
    from factory.dashboard.hub_client import DashboardHubClient


def _e2e_enabled() -> bool:
    return os.environ.get("FACTORY_DASHBOARD_E2E", "").strip() in {"1", "true", "yes"}


def _stub_agents_status(agents: list[str]) -> AgentHealthResponse:
    return AgentHealthResponse(
        agents=[
            AgentHealth(
                agent=name,
                in_roster=True,
                harness="claude-cli",
                harness_reachable=True,
                online=True,
            )
            for name in agents
        ]
    )


def _hub_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


def build_bff_router(adapter: WebAdapter, hub: DashboardHubClient) -> APIRouter:
    router = APIRouter(prefix="/api/bff")

    @router.get("/agents/status")
    async def agents_status(
        harness: str | None = Query(default=None),
        agent: str | None = Query(default=None),
    ) -> dict:
        agents = adapter.agent_names
        if _e2e_enabled():
            return _stub_agents_status(agents).model_dump()
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

    return router