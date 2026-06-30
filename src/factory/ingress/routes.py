"""HTTP route handlers for factory-ingress."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from factory.ingress.config import IngressConfig
from factory.ingress.connectors.registry import ConnectorRegistry
from factory.ingress.orchestrator import handle_webhook
from factory.ingress.ports import InstallationRegistry, SecretResolver
from factory.ingress.publisher import EventPublisher


def build_router(
    cfg: IngressConfig,
    get_publisher: Callable[[], EventPublisher | None],
    *,
    registry: ConnectorRegistry,
    get_installations: Callable[[], InstallationRegistry],
    secrets: SecretResolver,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, bool | str]:
        flags = {name: entry.enabled for name, entry in cfg.connectors.items()}
        return {"ok": True, **flags}

    async def _dispatch(
        connector: str,
        request: Request,
        path_tenant: str | None = None,
    ) -> JSONResponse:
        return await handle_webhook(
            connector,
            headers=request.headers,
            body=await request.body(),
            path_tenant=path_tenant,
            config=cfg,
            registry=registry,
            installations=get_installations(),
            secrets=secrets,
            publisher=get_publisher(),
        )

    @router.post("/webhook/github")
    async def webhook_github_alias(request: Request) -> JSONResponse:
        return await _dispatch("github", request, path_tenant=None)

    @router.post("/webhook/cloudflare")
    async def webhook_cloudflare_alias(request: Request) -> JSONResponse:
        return await _dispatch("cloudflare", request, path_tenant="default")

    @router.post("/webhook/{connector}")
    async def webhook_connector(connector: str, request: Request) -> JSONResponse:
        plugin = registry.get(connector)
        if plugin is not None and plugin.family == "per_account":
            raise HTTPException(status_code=404, detail="tenant required")
        return await _dispatch(connector, request, path_tenant=None)

    @router.post("/webhook/{connector}/{tenant}")
    async def webhook_connector_tenant(
        connector: str, tenant: str, request: Request
    ) -> JSONResponse:
        return await _dispatch(connector, request, path_tenant=tenant)

    return router