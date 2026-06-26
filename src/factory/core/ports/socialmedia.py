"""SocialMediaClientProtocol — driven port for factory.tool.socialmedia.*."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from roxabi_contracts.blob_ref import BlobRef

if TYPE_CHECKING:
    from roxabi_contracts.socialmedia.models import (
        SocialMediaListGroupsResponse,
        SocialMediaListIntegrationsResponse,
        SocialMediaPlatform,
        SocialMediaPublishResponse,
    )


@runtime_checkable
class SocialMediaClientProtocol(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def is_available(self) -> bool: ...

    async def list_groups(
        self, *, trace_id: str | None = None, timeout: float | None = None
    ) -> "SocialMediaListGroupsResponse": ...

    async def list_integrations(
        self,
        *,
        brand_slug: str | None = None,
        trace_id: str | None = None,
        timeout: float | None = None,
    ) -> "SocialMediaListIntegrationsResponse": ...

    async def publish(  # noqa: PLR0913 — mirrors NatsSocialMediaClient / contract request shape
        self,
        *,
        brand_slug: str,
        content: str,
        platforms: list["SocialMediaPlatform"],
        integration_ids: list[str] | None = None,
        media: list[BlobRef] | None = None,
        short_link: bool = False,
        platform_settings: dict[str, dict[str, Any]] | None = None,
        tags: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
        timeout: float | None = None,
    ) -> "SocialMediaPublishResponse": ...

    async def schedule(  # noqa: PLR0913 — mirrors NatsSocialMediaClient / contract request shape
        self,
        *,
        brand_slug: str,
        content: str,
        platforms: list["SocialMediaPlatform"],
        publish_at: datetime,
        integration_ids: list[str] | None = None,
        media: list[BlobRef] | None = None,
        short_link: bool = False,
        platform_settings: dict[str, dict[str, Any]] | None = None,
        tags: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
        timeout: float | None = None,
    ) -> "SocialMediaPublishResponse": ...