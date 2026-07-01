"""NATS request-reply client for dashboard → hub RPC (#1771)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from roxabi_contracts.dashboard import (
    SUBJECTS,
    AgentHealthResponse,
    DashboardAdminAccessResponse,
    DashboardAdminUserCreateRequest,
    DashboardAdminUserPatchRequest,
    DashboardAdminUserResponse,
    DashboardAgentConfigResponse,
    DashboardAgentCreateRequest,
    DashboardAgentPatchRequest,
    DashboardAgentsListResponse,
    DashboardAgentSoulPreviewRequest,
    DashboardAgentSoulPreviewResponse,
    DashboardAgentSoulPutRequest,
    DashboardAgentSoulSectionsResponse,
    DashboardConnectorInstallationDeleteRequest,
    DashboardConnectorInstallationsListResponse,
    DashboardConnectorInstallationUpsertRequest,
    DashboardFleetResponse,
    DashboardJobsLaunchRequest,
    DashboardJobsLaunchResponse,
    DashboardJobsListResponse,
    DashboardJobsSteerRequest,
    DashboardJobsSteerResponse,
    DashboardPipelineResponse,
    DashboardSessionsListRequest,
    DashboardSessionsListResponse,
    DashboardSessionsResumeRequest,
    DashboardSessionsResumeResponse,
    DashboardSessionsTurnsRequest,
    DashboardSessionsTurnsResponse,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.adapters.web.web_adapter import WebAdapter

log = logging.getLogger(__name__)

_RPC_TIMEOUT = 5.0  # const-ok: dashboard hub RPC timeout


class HubAgentNotFoundError(LookupError):
    """Hub RPC returned ``{"error": "not_found"}`` for an agent config call."""


class HubAgentConflictError(ValueError):
    """Hub RPC returned ``{"error": "conflict"}`` for an agent create call."""


class HubUserNotFoundError(LookupError):
    """Hub RPC returned ``{"error": "not_found"}`` for an admin user call."""


class HubUserConflictError(ValueError):
    """Hub RPC returned ``{"error": "conflict"}`` for an admin user write call."""


class HubStoreUnavailableError(RuntimeError):
    """Hub RPC returned ``{"error": "store_unavailable"}``."""


def _raise_admin_user_rpc_error(raw: dict[str, Any]) -> None:
    err = raw.get("error")
    if err == "not_found":
        raise HubUserNotFoundError(str(raw.get("message") or "not_found"))
    if err == "conflict":
        raise HubUserConflictError(str(raw.get("message") or "conflict"))
    if err == "store_unavailable":
        raise HubStoreUnavailableError(str(raw.get("message") or "store_unavailable"))


def _raise_agent_rpc_error(raw: dict[str, Any]) -> None:
    err = raw.get("error")
    if err == "not_found":
        raise HubAgentNotFoundError(str(raw.get("message") or "not_found"))
    if err == "conflict":
        raise HubAgentConflictError(str(raw.get("message") or "conflict"))


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

    async def launch_job(
        self,
        *,
        agent: str,
        prompt: str,
        job_name: str = "omp",
        model: str | None = None,
    ) -> DashboardJobsLaunchResponse:
        req = DashboardJobsLaunchRequest(
            agent=agent,
            prompt=prompt,
            job_name=job_name,
            model=model,
        )
        raw = await self._request(SUBJECTS.jobs_launch, req.model_dump())
        return DashboardJobsLaunchResponse.model_validate(raw)

    async def steer_job(self, job_id: str, text: str) -> DashboardJobsSteerResponse:
        req = DashboardJobsSteerRequest(job_id=job_id, text=text)
        raw = await self._request(SUBJECTS.jobs_steer, req.model_dump())
        return DashboardJobsSteerResponse.model_validate(raw)

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

    async def list_agent_configs(self) -> DashboardAgentsListResponse:
        raw = await self._request(SUBJECTS.agents_list, {})
        return DashboardAgentsListResponse.model_validate(raw)

    async def create_agent_config(
        self, body: DashboardAgentCreateRequest
    ) -> DashboardAgentConfigResponse:
        raw = await self._request(
            SUBJECTS.agents_create, {"body": body.model_dump()}
        )
        _raise_agent_rpc_error(raw)
        return DashboardAgentConfigResponse.model_validate(raw)

    async def list_admin_access(self) -> DashboardAdminAccessResponse:
        raw = await self._request(SUBJECTS.admin_access, {})
        return DashboardAdminAccessResponse.model_validate(raw)

    async def create_admin_user(
        self, body: DashboardAdminUserCreateRequest
    ) -> DashboardAdminUserResponse:
        raw = await self._request(
            SUBJECTS.admin_user_create, {"body": body.model_dump()}
        )
        _raise_admin_user_rpc_error(raw)
        return DashboardAdminUserResponse.model_validate(raw)

    async def patch_admin_user(
        self, user_id: str, patch: DashboardAdminUserPatchRequest
    ) -> DashboardAdminUserResponse:
        raw = await self._request(
            SUBJECTS.admin_user_patch,
            {"user_id": user_id, "patch": patch.model_dump(exclude_unset=True)},
        )
        _raise_admin_user_rpc_error(raw)
        return DashboardAdminUserResponse.model_validate(raw)

    async def get_agent_config(self, name: str) -> DashboardAgentConfigResponse:
        raw = await self._request(SUBJECTS.agents_get, {"name": name})
        _raise_agent_rpc_error(raw)
        return DashboardAgentConfigResponse.model_validate(raw)

    async def patch_agent_config(
        self, name: str, patch: DashboardAgentPatchRequest
    ) -> DashboardAgentConfigResponse:
        raw = await self._request(
            SUBJECTS.agents_patch,
            {"name": name, "patch": patch.model_dump(exclude_unset=True)},
        )
        _raise_agent_rpc_error(raw)
        return DashboardAgentConfigResponse.model_validate(raw)

    async def put_agent_soul(
        self, name: str, body: DashboardAgentSoulPutRequest
    ) -> DashboardAgentSoulSectionsResponse:
        raw = await self._request(
            SUBJECTS.agents_soul_put,
            {"name": name, "body": body.model_dump()},
        )
        _raise_agent_rpc_error(raw)
        return DashboardAgentSoulSectionsResponse.model_validate(raw)

    async def get_agent_soul(self, name: str) -> DashboardAgentSoulSectionsResponse:
        raw = await self._request(SUBJECTS.agents_soul_get, {"name": name})
        _raise_agent_rpc_error(raw)
        return DashboardAgentSoulSectionsResponse.model_validate(raw)

    async def preview_agent_soul(
        self, name: str, body: DashboardAgentSoulPreviewRequest
    ) -> DashboardAgentSoulPreviewResponse:
        raw = await self._request(
            SUBJECTS.agents_soul_preview,
            {"name": name, **body.model_dump()},
        )
        return DashboardAgentSoulPreviewResponse.model_validate(raw)

    async def fleet_list(self) -> DashboardFleetResponse:
        raw = await self._request(SUBJECTS.fleet_list, {})
        return DashboardFleetResponse.model_validate(raw)

    async def pipeline_list(self) -> DashboardPipelineResponse:
        raw = await self._request(SUBJECTS.pipeline_list, {})
        return DashboardPipelineResponse.model_validate(raw)

    async def list_connector_installations(
        self, connector: str, *, factory_tenant: str
    ) -> DashboardConnectorInstallationsListResponse:
        raw = await self._request(
            SUBJECTS.connectors_installations_list,
            {"connector": connector, "factory_tenant": factory_tenant},
        )
        if raw.get("error"):
            raise RuntimeError(str(raw["error"]))
        return DashboardConnectorInstallationsListResponse.model_validate(raw)

    async def upsert_connector_installation(
        self, req: DashboardConnectorInstallationUpsertRequest
    ) -> dict[str, Any]:
        return await self._request(
            SUBJECTS.connectors_installations_upsert, req.model_dump()
        )

    async def delete_connector_installation(
        self, req: DashboardConnectorInstallationDeleteRequest
    ) -> dict[str, Any]:
        return await self._request(
            SUBJECTS.connectors_installations_delete, req.model_dump()
        )
