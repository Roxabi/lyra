"""Agent config + soul BFF routes — /api/bff/agents* (+ voice capabilities)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from factory.dashboard.hub_client import HubAgentConflictError, HubAgentNotFoundError
from factory.dashboard.routes.bff_common import map_hub_errors
from roxabi_contracts.dashboard import (
    DashboardAgentCreateRequest,
    DashboardAgentPatchRequest,
    DashboardAgentSoulPreviewRequest,
    DashboardAgentSoulPutRequest,
)

if TYPE_CHECKING:
    from factory.dashboard.hub_client import DashboardHubClient


def register_agent_routes(  # noqa: C901, PLR0915
    router: APIRouter, hub: DashboardHubClient
) -> None:
    @router.get("/voice/capabilities")
    async def voice_capabilities() -> dict:
        """Dynamic TTS engines + Grok voices + clone samples from voice workers."""
        try:
            return (await hub.voice_capabilities()).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.get("/agents")
    async def list_agents_config() -> dict:
        try:
            return (await hub.list_agent_configs()).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.post("/agents")
    async def create_agent_config(body: DashboardAgentCreateRequest) -> dict:
        try:
            return (await hub.create_agent_config(body)).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc, conflict=HubAgentConflictError)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.get("/agents/{name}")
    async def get_agent_config(name: str) -> dict:
        try:
            return (await hub.get_agent_config(name)).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc, not_found=HubAgentNotFoundError)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.patch("/agents/{name}")
    async def patch_agent_config(name: str, body: DashboardAgentPatchRequest) -> dict:
        try:
            return (await hub.patch_agent_config(name, body)).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc, not_found=HubAgentNotFoundError)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.put("/agents/{name}/soul")
    async def put_agent_soul(name: str, body: DashboardAgentSoulPutRequest) -> dict:
        try:
            return (await hub.put_agent_soul(name, body)).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc, not_found=HubAgentNotFoundError)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.get("/agents/{name}/soul")
    async def get_agent_soul(name: str) -> dict:
        try:
            return (await hub.get_agent_soul(name)).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc, not_found=HubAgentNotFoundError)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.post("/agents/{name}/soul/preview")
    async def preview_agent_soul(
        name: str, body: DashboardAgentSoulPreviewRequest
    ) -> dict:
        try:
            return (await hub.preview_agent_soul(name, body)).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc, not_found=HubAgentNotFoundError)
            if mapped is not None:
                raise mapped from exc
            raise