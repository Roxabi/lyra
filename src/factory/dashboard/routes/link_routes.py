"""Platform link BFF routes (ADR-103 Block 8)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from factory.core.auth.control_plane import ControlPlanePrincipal
from factory.dashboard.auth import control_plane_from_app, require_principal

__all__ = ["register_link_routes"]


class CreateLinkCodeBody(BaseModel):
    platform: str | None = Field(
        default=None, description="telegram | discord | null (any)"
    )


def _cp_or_503(request: Request):
    cp = control_plane_from_app(request)
    if cp is None:
        raise HTTPException(
            status_code=503, detail="control-plane identity store not configured"
        )
    return cp


async def _status(
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    links = await cp.list_platform_links(principal.user_id)
    ready = await cp.chat_ready(principal.user_id)
    return {
        "user_id": principal.user_id,
        "links": links,
        "chat_ready": ready,
        "required": ["telegram", "discord"],
    }


async def _create_code(
    body: CreateLinkCodeBody,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    try:
        code_id, token = await cp.create_link_code(
            principal.user_id, platform=body.platform
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "code_id": code_id,
        "token": token,
        "platform": body.platform,
        "instructions": (
            f"Send `/link {token}` to the factory bot on the target platform "
            "within 10 minutes."
        ),
    }


async def _unlink(
    platform: str,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    try:
        ok = await cp.unlink_platform(principal.user_id, platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="no link for platform")
    return {"status": "unlinked", "platform": platform}


def register_link_routes(router: APIRouter) -> None:
    router.add_api_route("/auth/links", _status, methods=["GET"])
    router.add_api_route("/auth/links/code", _create_code, methods=["POST"])
    router.add_api_route("/auth/links/{platform}", _unlink, methods=["DELETE"])
