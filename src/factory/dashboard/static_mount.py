"""Static SPA mount for apps/dashboard-v2/dist."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_DIST = Path(__file__).resolve().parents[3] / "apps" / "dashboard-v2" / "dist"


def dist_dir() -> Path:
    return _DIST


def dist_available() -> bool:
    return (_DIST / "index.html").is_file()


def mount_dashboard_static(app: FastAPI) -> None:
    """Mount built SPA when dist/index.html exists."""
    if not dist_available():
        return
    assets = _DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="dashboard-assets")
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
