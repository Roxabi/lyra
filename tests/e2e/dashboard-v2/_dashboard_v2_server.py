"""Standalone uvicorn entry for Playwright visual tests (dashboard-v2)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from factory.adapters.web.chat_routes import build_chat_router
from factory.adapters.web.web_adapter import WebAdapter
from factory.dashboard.hub_client import DashboardHubClient
from factory.dashboard.routes import build_bff_router, build_connectors_router
from factory.dashboard.stream_tokens import StreamTokenRegistry

_DIST = Path(__file__).resolve().parents[3] / "apps" / "dashboard-v2" / "dist"


def _dist_available() -> bool:
    return (_DIST / "index.html").is_file()


def _mount_dashboard_v2_static(app: FastAPI) -> None:
    """Mount built SPA when dist/index.html exists."""
    if not _dist_available():
        return
    assets = _DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="dashboard-v2-assets")

    @app.get("/")
    async def spa_index() -> FileResponse:
        return FileResponse(_DIST / "index.html")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404)
        candidate = _DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")


def create_dashboard_v2_app(adapter: WebAdapter) -> FastAPI:
    """Build factory-dashboard-v2 FastAPI app (BFF + dashboard-v2/dist SPA)."""
    app = FastAPI(title="Factory Dashboard v2", docs_url=None, redoc_url=None)
    tokens = StreamTokenRegistry()
    hub = DashboardHubClient(adapter)

    app.include_router(build_chat_router(adapter, tokens))
    app.include_router(build_bff_router(adapter, hub, tokens))
    app.include_router(build_connectors_router(hub))

    if not _dist_available():
        from factory.dashboard.smoke_html import smoke_index

        @app.get("/")
        async def smoke_fallback() -> str:
            return smoke_index()

    _mount_dashboard_v2_static(app)
    return app


def main() -> None:
    port = int(sys.argv[1])
    os.environ.setdefault("FACTORY_DASHBOARD_E2E", "1")
    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(
        inbound_bus=bus,
        agent_names=["alpha", "beta"],
        port=19999,
    )
    listener = MagicMock()
    listener.cache_inbound = MagicMock()
    adapter._outbound_listener = listener
    uvicorn.run(
        create_dashboard_v2_app(adapter),
        host="127.0.0.1",
        port=port,
        log_level="error",
    )


if __name__ == "__main__":
    main()
