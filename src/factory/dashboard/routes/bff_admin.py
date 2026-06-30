"""Admin user BFF routes — /api/bff/admin/*."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from factory.dashboard.hub_client import (
    HubStoreUnavailableError,
    HubUserConflictError,
    HubUserNotFoundError,
)
from factory.dashboard.routes.bff_common import map_hub_errors
from roxabi_contracts.dashboard import (
    DashboardAdminUserCreateRequest,
    DashboardAdminUserPatchRequest,
)

if TYPE_CHECKING:
    from factory.dashboard.hub_client import DashboardHubClient


def register_admin_routes(router: APIRouter, hub: DashboardHubClient) -> None:
    @router.get("/admin/access")
    async def admin_access() -> dict:
        try:
            return (await hub.list_admin_access()).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    @router.post("/admin/users")
    async def create_admin_user(body: DashboardAdminUserCreateRequest) -> dict:
        try:
            return (await hub.create_admin_user(body)).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(
                exc,
                conflict=HubUserConflictError,
                store_unavailable=HubStoreUnavailableError,
            )
            if mapped is not None:
                raise mapped from exc
            raise

    @router.patch("/admin/users/{user_id}")
    async def patch_admin_user(
        user_id: str, body: DashboardAdminUserPatchRequest
    ) -> dict:
        try:
            return (await hub.patch_admin_user(user_id, body)).model_dump()
        except Exception as exc:
            mapped = map_hub_errors(
                exc,
                not_found=HubUserNotFoundError,
                conflict=HubUserConflictError,
                store_unavailable=HubStoreUnavailableError,
            )
            if mapped is not None:
                raise mapped from exc
            raise