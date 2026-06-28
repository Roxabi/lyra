"""NATS request-reply client for dashboard → hub RPC (#1771)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from roxabi_contracts.dashboard import (
    SUBJECTS,
    AgentHealthResponse,
    DashboardSessionsListRequest,
    DashboardSessionsListResponse,
    DashboardSessionsResumeRequest,
    DashboardSessionsResumeResponse,
    DashboardJobsListResponse,
    DashboardSessionsTurnsRequest,
    DashboardSessionsTurnsResponse,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.adapters.web.web_adapter import WebAdapter

log = logging.getLogger(__name__)

_RPC_TIMEOUT = 5.0  # const-ok: dashboard hub RPC timeout


class DashboardHubClient:
    """Thin NATS RPC facade — reads live NATS handle from the adapter."""

    def __init__(self, adapter: WebAdapter) -> None:
        self._adapter = adapter

    def _nc(self) -> NATS | None:
        return getattr(self._adapter, "_nats_client", None)

    async def _request(self, subject: str, payload: dict[str, Any]) -> dict[str, Any]:
        nc = self._nc()
        if nc is None:
            raise RuntimeError("NATS client not wired")
        msg = await nc.request(
            subject,
            json.dumps(payload).encode(),
            timeout=_RPC_TIMEOUT,
        )
        return json.loads(msg.data.decode())

    async def list_sessions(
        self, agent: str, *, limit: int = 20
    ) -> DashboardSessionsListResponse:
        req = DashboardSessionsListRequest(agent=agent, limit=limit)
        raw = await self._request(SUBJECTS.sessions_list, req.model_dump())
        return DashboardSessionsListResponse.model_validate(raw)

    async def list_jobs(self) -> DashboardJobsListResponse:
        raw = await self._request(SUBJECTS.jobs_list, {})
        return DashboardJobsListResponse.model_validate(raw)

    async def list_turns(
        self, session_id: str, *, limit: int = 200
    ) -> DashboardSessionsTurnsResponse:
        req = DashboardSessionsTurnsRequest(session_id=session_id, limit=limit)
        raw = await self._request(SUBJECTS.sessions_turns, req.model_dump())
        return DashboardSessionsTurnsResponse.model_validate(raw)

    async def resume_session(
        self, agent: str, cli_session_id: str
    ) -> DashboardSessionsResumeResponse:
        req = DashboardSessionsResumeRequest(agent=agent, cli_session_id=cli_session_id)
        raw = await self._request(SUBJECTS.sessions_resume, req.model_dump())
        return DashboardSessionsResumeResponse.model_validate(raw)

    async def agents_status(
        self,
        agents: list[str],
        *,
        harness_by_agent: dict[str, str] | None = None,
    ) -> AgentHealthResponse:
        payload: dict[str, Any] = {"agents": agents}
        if harness_by_agent:
            payload["harness_by_agent"] = harness_by_agent
        raw = await self._request(SUBJECTS.agents_status, payload)
        return AgentHealthResponse.model_validate(raw)