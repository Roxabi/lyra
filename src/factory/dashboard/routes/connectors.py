"""BFF routes for ingress connector onboarding (ADR-096 / #1992)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from factory.dashboard.auth import OperatorContext, require_operator
from factory.dashboard.e2e import (
    e2e_enabled,
    stub_connector_installations,
    stub_github_install_url,
)
from roxabi_contracts.dashboard import (
    DashboardConnectorInstallationDeleteRequest,
    DashboardConnectorInstallationUpsertRequest,
    DashboardGithubInstallUrlResponse,
)

if TYPE_CHECKING:
    from factory.dashboard.hub_client import DashboardHubClient


class ConnectorInstallationBody(BaseModel):
    external_id: str = Field(min_length=1, max_length=128)
    factory_tenant: str | None = None
    metadata: dict[str, str] | None = None


_SUPPORTED = frozenset({"github", "cloudflare"})


def _github_app_slug() -> str | None:
    raw = os.environ.get("INGRESS_GITHUB_APP_SLUG", "").strip()
    return raw or None


def build_connectors_router(hub: DashboardHubClient) -> APIRouter:  # noqa: C901
    router = APIRouter(prefix="/api/bff/connectors")

    @router.get("")
    async def list_connectors(
        operator: OperatorContext = Depends(require_operator),
    ) -> dict[str, object]:
        return {
            "connectors": [
                {
                    "name": "github",
                    "family": "centralized_app",
                    "label": "GitHub App",
                },
                {
                    "name": "cloudflare",
                    "family": "per_account",
                    "label": "Cloudflare Notifications",
                },
            ],
            "factory_tenant": operator.factory_tenant,
        }

    @router.get("/github/install-url")
    async def github_install_url(
        operator: OperatorContext = Depends(require_operator),
    ) -> DashboardGithubInstallUrlResponse:
        if e2e_enabled():
            return stub_github_install_url()
        slug = _github_app_slug()
        if not slug:
            raise HTTPException(
                status_code=503,
                detail="INGRESS_GITHUB_APP_SLUG not configured",
            )
        return DashboardGithubInstallUrlResponse(
            url=f"https://github.com/apps/{slug}/installations/new",
            app_slug=slug,
        )

    @router.get("/{connector}/installations")
    async def list_installations(
        connector: str,
        operator: OperatorContext = Depends(require_operator),
    ) -> dict:
        if connector not in _SUPPORTED:
            raise HTTPException(status_code=404, detail="unknown connector")
        if e2e_enabled():
            return stub_connector_installations(connector, operator.factory_tenant)
        try:
            resp = await hub.list_connector_installations(
                connector, factory_tenant=operator.factory_tenant
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return resp.model_dump()

    @router.post("/{connector}/installations")
    async def upsert_installation(
        connector: str,
        body: ConnectorInstallationBody,
        operator: OperatorContext = Depends(require_operator),
    ) -> dict:
        if connector not in _SUPPORTED:
            raise HTTPException(status_code=404, detail="unknown connector")
        tenant = body.factory_tenant or operator.factory_tenant
        if tenant != operator.factory_tenant:
            raise HTTPException(status_code=403, detail="tenant forbidden")
        if e2e_enabled():
            return {"ok": True}
        req = DashboardConnectorInstallationUpsertRequest(
            connector=connector,
            external_id=body.external_id,
            factory_tenant=tenant,
            operator_tenant=operator.factory_tenant,
            metadata=body.metadata,
        )
        try:
            raw = await hub.upsert_connector_installation(req)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if raw.get("error") == "tenant_forbidden":
            raise HTTPException(status_code=403, detail="tenant forbidden")
        if raw.get("error"):
            raise HTTPException(status_code=400, detail=str(raw["error"]))
        return raw

    @router.delete("/{connector}/installations/{external_id}")
    async def delete_installation(
        connector: str,
        external_id: str,
        operator: OperatorContext = Depends(require_operator),
    ) -> dict:
        if connector not in _SUPPORTED:
            raise HTTPException(status_code=404, detail="unknown connector")
        if e2e_enabled():
            return {"ok": True}
        req = DashboardConnectorInstallationDeleteRequest(
            connector=connector,
            external_id=external_id,
            operator_tenant=operator.factory_tenant,
        )
        try:
            raw = await hub.delete_connector_installation(req)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if raw.get("error") == "tenant_forbidden":
            raise HTTPException(status_code=403, detail="tenant forbidden")
        return raw

    return router