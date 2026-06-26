"""NatsSocialMediaClient — hub client for factory.tool.socialmedia.*."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from factory.nats.socialmedia.nats_socialmedia_codec import (
    SocialMediaCodec,
    SocialMediaResult,
)
from roxabi_contracts.blob_ref import BlobRef
from roxabi_contracts.socialmedia import SUBJECTS
from roxabi_contracts.socialmedia.models import (
    SocialMediaListGroupsRequest,
    SocialMediaListGroupsResponse,
    SocialMediaListIntegrationsRequest,
    SocialMediaListIntegrationsResponse,
    SocialMediaPlatform,
    SocialMediaPublishRequest,
    SocialMediaPublishResponse,
    SocialMediaScheduleRequest,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.transport.worker_pool_client import WorkerPoolClient

log = logging.getLogger(__name__)

__all__ = [
    "NatsSocialMediaClient",
    "SocialMediaUnavailableError",
    "SUBJECTS",
]


class SocialMediaUnavailableError(Exception):
    """Raised when the socialmedia tool plane cannot satisfy a request."""


class NatsSocialMediaClient:
    def __init__(
        self,
        pool: "WorkerPoolClient",
        codec: SocialMediaCodec,
        nc: "NATS | None" = None,
        *,
        default_timeout: float = 90.0,
    ) -> None:
        self._pool = pool
        self._codec = codec
        self._nc = nc
        self._default_timeout = default_timeout

    async def start(self) -> None:
        if self._nc is None:
            raise RuntimeError(
                "NatsSocialMediaClient.start() called without nc at __init__"
            )
        await self._pool.start(self._nc)

    async def stop(self) -> None:
        await self._pool.stop()

    def is_available(self) -> bool:
        return self._pool.is_pool_alive()

    async def list_groups(
        self, *, trace_id: str | None = None, timeout: float | None = None
    ) -> SocialMediaListGroupsResponse:
        req = SocialMediaListGroupsRequest(
            **self._codec.envelope_fields(trace_id=trace_id),
            request_id=str(uuid4()),
        )
        return await self._call(
            SUBJECTS.list_groups,
            req,
            SocialMediaListGroupsResponse,
            timeout=timeout,
        )

    async def list_integrations(
        self,
        *,
        brand_slug: str | None = None,
        trace_id: str | None = None,
        timeout: float | None = None,
    ) -> SocialMediaListIntegrationsResponse:
        req = SocialMediaListIntegrationsRequest(
            **self._codec.envelope_fields(trace_id=trace_id),
            request_id=str(uuid4()),
            brand_slug=brand_slug,
        )
        return await self._call(
            SUBJECTS.list_integrations,
            req,
            SocialMediaListIntegrationsResponse,
            timeout=timeout,
        )

    async def publish(  # noqa: PLR0913 — contract/request shape
        self,
        *,
        brand_slug: str,
        content: str,
        platforms: list[SocialMediaPlatform],
        integration_ids: list[str] | None = None,
        media: list[BlobRef] | None = None,
        short_link: bool = False,
        platform_settings: dict[str, dict[str, Any]] | None = None,
        tags: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
        timeout: float | None = None,
    ) -> SocialMediaPublishResponse:
        req = SocialMediaPublishRequest(
            **self._codec.envelope_fields(trace_id=trace_id),
            request_id=str(uuid4()),
            brand_slug=brand_slug,
            content=content,
            platforms=platforms,
            integration_ids=integration_ids,
            media=media or [],
            short_link=short_link,
            platform_settings=platform_settings or {},
            tags=tags or [],
        )
        return await self._call(
            SUBJECTS.publish,
            req,
            SocialMediaPublishResponse,
            timeout=timeout,
        )

    async def schedule(  # noqa: PLR0913 — contract/request shape
        self,
        *,
        brand_slug: str,
        content: str,
        platforms: list[SocialMediaPlatform],
        publish_at: datetime,
        integration_ids: list[str] | None = None,
        media: list[BlobRef] | None = None,
        short_link: bool = False,
        platform_settings: dict[str, dict[str, Any]] | None = None,
        tags: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
        timeout: float | None = None,
    ) -> SocialMediaPublishResponse:
        req = SocialMediaScheduleRequest(
            **self._codec.envelope_fields(trace_id=trace_id),
            request_id=str(uuid4()),
            brand_slug=brand_slug,
            content=content,
            platforms=platforms,
            publish_at=publish_at,
            integration_ids=integration_ids,
            media=media or [],
            short_link=short_link,
            platform_settings=platform_settings or {},
            tags=tags or [],
        )
        return await self._call(
            SUBJECTS.schedule,
            req,
            SocialMediaPublishResponse,
            timeout=timeout,
        )

    async def _call(
        self,
        subject: str,
        request: Any,
        response_model: type,
        *,
        timeout: float | None,
    ) -> Any:
        payload = self._codec.encode(request)
        raw = await self._pool.request(
            subject, payload, timeout=timeout or self._default_timeout
        )
        decoded: SocialMediaResult = self._codec.decode(raw, response_model)
        if decoded.error or decoded.response is None:
            raise SocialMediaUnavailableError(decoded.error or "socialmedia.error")
        return decoded.response