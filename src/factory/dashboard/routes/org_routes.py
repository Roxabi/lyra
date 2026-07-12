"""Organization BFF routes (ADR-103 Block 4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from factory.core.auth.control_plane import ControlPlanePrincipal
from factory.core.auth.control_plane_authz import authorize
from factory.core.auth.control_plane_org import OrgRole
from factory.dashboard.auth import control_plane_from_app, require_principal

__all__ = ["register_org_routes"]


class CreateOrgBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AddMemberBody(BaseModel):
    user_id: str
    org_role: str = OrgRole.MEMBER.value


def _cp_or_503(request: Request):
    cp = control_plane_from_app(request)
    if cp is None:
        raise HTTPException(
            status_code=503, detail="control-plane identity store not configured"
        )
    return cp


async def _create_org(
    body: CreateOrgBody,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    decision = authorize(principal, "orgs.create")
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    cp = _cp_or_503(request)
    try:
        org = await cp.create_org(name=body.name, created_by=principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "org": {
            "id": org.id,
            "name": org.name,
            "created_by": org.created_by,
            "created_at": org.created_at.isoformat(),
        }
    }


async def _list_orgs(
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    if principal.is_admin:
        # Admin: all orgs via list of memberships of self is incomplete;
        # list orgs user belongs to still OK; expand later if needed.
        orgs = await cp.list_orgs_for_user(principal.user_id)
    else:
        orgs = await cp.list_orgs_for_user(principal.user_id)
    return {
        "orgs": [
            {
                "id": o.id,
                "name": o.name,
                "created_by": o.created_by,
                "created_at": o.created_at.isoformat(),
            }
            for o in orgs
        ],
        "active_org_id": principal.active_org_id,
    }


async def _list_members(
    org_id: str,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    if not principal.is_admin and not await cp.is_org_member(org_id, principal.user_id):
        raise HTTPException(status_code=403, detail="not a member")
    members = await cp.list_org_members(org_id)
    return {
        "members": [
            {
                "org_id": m.org_id,
                "user_id": m.user_id,
                "org_role": m.org_role.value,
            }
            for m in members
        ]
    }


async def _add_member(
    org_id: str,
    body: AddMemberBody,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    try:
        role = OrgRole(body.org_role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid org_role") from exc
    try:
        member = await cp.add_org_member(
            org_id,
            body.user_id,
            org_role=role,
            actor_user_id=principal.user_id,
            actor_is_admin=principal.is_admin,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "member": {
            "org_id": member.org_id,
            "user_id": member.user_id,
            "org_role": member.org_role.value,
        }
    }


async def _remove_member(
    org_id: str,
    user_id: str,
    request: Request,
    principal: ControlPlanePrincipal = Depends(require_principal),
) -> dict[str, Any]:
    cp = _cp_or_503(request)
    try:
        ok = await cp.remove_org_member(
            org_id,
            user_id,
            actor_user_id=principal.user_id,
            actor_is_admin=principal.is_admin,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="member not found")
    return {"status": "removed", "org_id": org_id, "user_id": user_id}


def register_org_routes(router: APIRouter) -> None:
    router.add_api_route("/orgs", _create_org, methods=["POST"])
    router.add_api_route("/orgs", _list_orgs, methods=["GET"])
    router.add_api_route("/orgs/{org_id}/members", _list_members, methods=["GET"])
    router.add_api_route("/orgs/{org_id}/members", _add_member, methods=["POST"])
    router.add_api_route(
        "/orgs/{org_id}/members/{user_id}",
        _remove_member,
        methods=["DELETE"],
    )
