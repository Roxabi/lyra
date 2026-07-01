"""Composition root — chat adapter axis + BFF axis + SPA static (#1771)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI

from factory.adapters.web.chat_routes import build_chat_router
from factory.dashboard.hub_client import DashboardHubClient
from factory.dashboard.routes import build_bff_router, build_connectors_router
from factory.dashboard.static_mount import dist_available, mount_dashboard_static
from factory.dashboard.stream_tokens import StreamTokenRegistry

if TYPE_CHECKING:
    from factory.adapters.web.web_adapter import WebAdapter


def create_dashboard_app(adapter: WebAdapter) -> FastAPI:
    """Build the factory-dashboard FastAPI app (ADR-094 two axes, one process)."""
    app = FastAPI(title="Factory Dashboard", docs_url=None, redoc_url=None)
    tokens = StreamTokenRegistry()
    hub = DashboardHubClient(adapter)

    app.include_router(build_chat_router(adapter, tokens))
    app.include_router(build_bff_router(adapter, hub, tokens))
    app.include_router(build_connectors_router(hub))

    if not dist_available():
        from factory.dashboard.smoke_html import smoke_index

        @app.get("/")
        async def smoke_fallback() -> str:
            return smoke_index()

    mount_dashboard_static(app)
    return app