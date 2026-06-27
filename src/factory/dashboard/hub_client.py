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
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)

_RPC_TIMEOUT = 5.0  # const-ok: dashboard hub RPC timeout


class DashboardHubClient:
    """Thin NATS RPC facade — no TurnStore access from dashboard process."""

    def __init__(self, nc: NATS | None) -> None:
        self._nc = nc

    async def _request(self, subject: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._nc is None:
            raise RuntimeError("NATS client not wired")
        msg = await self._nc.request(
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

    async def resume_session(
        self, agent: str, cli_session_id: str
    ) -> DashboardSessionsResumeResponse:
        req = DashboardSessionsResumeRequest(agent=agent, cli_session_id=cli_session_id)
        raw = await self._request(SUBJECTS.sessions_resume, req.model_dump())
        return DashboardSessionsResumeResponse.model_validate(raw)

    async def agents_status(self, agents: list[str]) -> AgentHealthResponse:
        raw = await self._request(SUBJECTS.agents_status, {"agents": agents})
        return AgentHealthResponse.model_validate(raw)