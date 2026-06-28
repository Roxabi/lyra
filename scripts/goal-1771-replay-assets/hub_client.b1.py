"""Dashboard hub RPC client (#1771) — Block 1 slice (agents/status only)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from roxabi_contracts.dashboard import AgentHealthResponse

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.adapters.web.web_adapter import WebAdapter

log = logging.getLogger(__name__)

_RPC_TIMEOUT = 5.0  # const-ok: dashboard hub RPC timeout


class DashboardHubClient:
    """Thin NATS RPC facade — Block 1 exposes agents_status only."""

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

    async def agents_status(
        self,
        agents: list[str],
        *,
        harness_by_agent: dict[str, str] | None = None,
    ) -> AgentHealthResponse:
        from roxabi_contracts.dashboard import SUBJECTS

        payload: dict[str, Any] = {"agents": agents}
        if harness_by_agent:
            payload["harness_by_agent"] = harness_by_agent
        raw = await self._request(SUBJECTS.agents_status, payload)
        return AgentHealthResponse.model_validate(raw)