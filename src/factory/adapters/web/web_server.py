"""FastAPI surface for the web adapter — delegates to factory.dashboard.app."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

if TYPE_CHECKING:
    from factory.adapters.web.web_adapter import WebAdapter


def create_app(adapter: "WebAdapter") -> FastAPI:
    from factory.dashboard.app import create_dashboard_app

    return create_dashboard_app(adapter)


async def run_uvicorn(
    app: FastAPI,
    *,
    host: str,
    port: int,
    server_holder: Any,
) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server_holder._uvicorn_server = server
    await server.serve()