"""Postiz Public API client — v1 backing provider for tool.socialmedia."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from factory.adapters.socialmedia.slug import resolve_group_id, slugify_brand


class PostizApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class PostizPublicApiClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 60.0) -> None:
        root = base_url.rstrip("/")
        self._api = f"{root}/public/v1"
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": api_key},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_groups(self) -> list[dict[str, Any]]:
        data = await self._get("groups")
        if not isinstance(data, list):
            raise PostizApiError("unexpected groups response", body=data)
        return data

    async def list_integrations(self, *, group_id: str | None = None) -> list[dict]:
        path = "integrations"
        if group_id:
            data = await self._get(path, params={"group": group_id})
        else:
            data = await self._get(path)
        if not isinstance(data, list):
            raise PostizApiError("unexpected integrations response", body=data)
        return data

    async def upload_bytes(
        self, *, filename: str, content: bytes, content_type: str
    ) -> dict[str, Any]:
        files = {"file": (filename, content, content_type)}
        resp = await self._client.post(f"{self._api}/upload", files=files)
        return self._parse(resp)

    async def create_post(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        payload = {**body, "creationMethod": "API"}
        data = await self._post("posts", payload)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        raise PostizApiError("unexpected create post response", body=data)

    async def resolve_integrations(
        self,
        *,
        brand_slug: str,
        platforms: list[str],
        integration_ids: list[str] | None,
    ) -> tuple[str | None, list[dict]]:
        groups = await self.list_groups()
        group_id = resolve_group_id(groups, brand_slug)
        integrations = await self.list_integrations(group_id=group_id)
        if integration_ids:
            wanted = set(integration_ids)
            picked = [i for i in integrations if i.get("id") in wanted]
            return group_id, picked
        platform_set = set(platforms)
        picked = [
            i
            for i in integrations
            if i.get("identifier") in platform_set and not i.get("disabled")
        ]
        return group_id, picked

    def build_post_payload(
        self,
        *,
        post_type: str,
        content: str,
        date: datetime,
        integrations: list[dict],
        media_items: list[dict[str, Any]],
        short_link: bool,
        tags: list[dict[str, str]],
        platform_settings: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        posts = []
        for integration in integrations:
            platform = integration.get("identifier")
            if not isinstance(platform, str):
                continue
            settings = platform_settings.get(platform) or {"__type": platform}
            if "__type" not in settings:
                settings = {**settings, "__type": platform}
            posts.append(
                {
                    "integration": {"id": integration["id"]},
                    "value": [{"content": content, "image": media_items}],
                    "settings": settings,
                }
            )
        return {
            "type": post_type,
            "date": date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "shortLink": short_link,
            "tags": tags,
            "posts": posts,
        }

    @staticmethod
    def map_group(group: dict[str, Any]) -> dict[str, str]:
        name = str(group.get("name", ""))
        return {
            "id": str(group["id"]),
            "name": name,
            "slug": slugify_brand(name),
        }

    @staticmethod
    def map_integration(
        integration: dict[str, Any], *, brand_slug: str | None, group_id: str | None
    ) -> dict[str, Any]:
        customer = integration.get("customer") or {}
        return {
            "id": str(integration["id"]),
            "name": str(integration.get("name", "")),
            "platform": str(integration.get("identifier", "")),
            "group_id": group_id or customer.get("id"),
            "group_slug": brand_slug,
            "disabled": bool(integration.get("disabled")),
        }

    async def _get(self, path: str, *, params: dict | None = None) -> Any:
        resp = await self._client.get(f"{self._api}/{path}", params=params)
        return self._parse(resp)

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        resp = await self._client.post(f"{self._api}/{path}", json=body)
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> Any:
        try:
            data = resp.json()
        except ValueError:
            data = resp.text
        if resp.status_code >= 400:
            message = data.get("msg") if isinstance(data, dict) else str(data)
            raise PostizApiError(
                message or f"HTTP {resp.status_code}",
                status_code=resp.status_code,
                body=data,
            )
        return data