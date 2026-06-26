"""SocialMediaNatsAdapter — NATS satellite for factory.tool.socialmedia.*."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from factory.adapters.socialmedia.postiz_client import PostizApiError, PostizPublicApiClient

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.socialmedia import SUBJECTS
from roxabi_contracts.socialmedia.models import (
    SocialMediaListGroupsRequest,
    SocialMediaListGroupsResponse,
    SocialMediaListIntegrationsRequest,
    SocialMediaListIntegrationsResponse,
    SocialMediaPublishRequest,
    SocialMediaPublishResponse,
    SocialMediaScheduleRequest,
)
from roxabi_nats.adapter_base import NatsAdapterBase
from roxabi_satellite.envelope import coerce_envelope_fields, work_fields_from_request
from roxabi_satellite.socialmedia import (
    build_list_groups_error,
    build_list_integrations_error,
    build_publish_error,
    validate_list_groups_request,
    validate_list_integrations_request,
    validate_publish_request,
    validate_schedule_request,
    worker_error_from_http_provider,
)

log = logging.getLogger(__name__)

_ENVELOPE_NAME = "SocialMediaRequest"
_SCHEMA_VERSION = 1
_HEARTBEAT_INTERVAL = 5.0


class SocialMediaNatsAdapter(NatsAdapterBase):
    def __init__(
        self,
        postiz: PostizPublicApiClient,
        *,
        provider_base_url: str,
        upload_media,
        identity_name: str | None = "socialmedia-adapter",
    ) -> None:
        super().__init__(
            subject=SUBJECTS.publish,
            queue_group=SUBJECTS.workers,
            envelope_name=_ENVELOPE_NAME,
            schema_version=_SCHEMA_VERSION,
            heartbeat_subject=SUBJECTS.heartbeat,
            heartbeat_interval=_HEARTBEAT_INTERVAL,
            identity_name=identity_name,
            wait_ready=False,
        )
        self._postiz = postiz
        self._provider_base_url = provider_base_url
        self._upload_media = upload_media
        self._active_requests = 0

    def _extra_subjects(self) -> list[str]:
        return [
            SUBJECTS.list_groups,
            SUBJECTS.list_integrations,
            SUBJECTS.schedule,
        ]

    def heartbeat_payload(self) -> dict:
        base = super().heartbeat_payload()
        base.update(
            {
                "provider": "postiz",
                "provider_base_url": self._provider_base_url,
                "active_requests": self._active_requests,
            }
        )
        return base

    async def handle(self, msg: Any, payload: dict) -> None:
        self._active_requests += 1
        try:
            if msg.subject == SUBJECTS.list_groups:
                await self._reply_model(msg, await self._handle_list_groups(payload))
            elif msg.subject == SUBJECTS.list_integrations:
                await self._reply_model(msg, await self._handle_list_integrations(payload))
            elif msg.subject == SUBJECTS.schedule:
                await self._reply_model(msg, await self._handle_schedule(payload))
            else:
                await self._reply_model(msg, await self._handle_publish(payload))
        finally:
            self._active_requests = max(0, self._active_requests - 1)

    async def _handle_list_groups(self, payload: dict) -> SocialMediaListGroupsResponse:
        outcome = validate_list_groups_request(payload)
        if outcome.error is not None or outcome.request is None:
            return build_list_groups_error(
                SocialMediaListGroupsRequest.model_construct(
                    **coerce_envelope_fields(
                        {"request_id": str(payload.get("request_id") or "unknown")}
                    )
                ),
                outcome.error or "malformed request",
            )
        req = outcome.request
        assert isinstance(req, SocialMediaListGroupsRequest)
        try:
            groups = [
                self._envelope_group(self._postiz.map_group(g))
                for g in await self._postiz.list_groups()
            ]
            return self._ok_list_groups(req, groups)
        except PostizApiError as exc:
            return build_list_groups_error(
                req,
                str(exc),
                worker_error=worker_error_from_http_provider(exc.status_code, str(exc)),
            )

    async def _handle_list_integrations(
        self, payload: dict
    ) -> SocialMediaListIntegrationsResponse:
        outcome = validate_list_integrations_request(payload)
        if outcome.error is not None or outcome.request is None:
            return build_list_integrations_error(
                SocialMediaListIntegrationsRequest.model_construct(
                    **coerce_envelope_fields(
                        {"request_id": str(payload.get("request_id") or "unknown")}
                    )
                ),
                outcome.error or "malformed request",
            )
        req = outcome.request
        assert isinstance(req, SocialMediaListIntegrationsRequest)
        try:
            group_id = None
            if req.brand_slug:
                group_id = await self._resolve_group_id(req.brand_slug)
                if group_id is None:
                    return SocialMediaListIntegrationsResponse(
                        **work_fields_from_request(req),
                        ok=True,
                        request_id=req.request_id,
                        integrations=[],
                    )
            raw = await self._postiz.list_integrations(group_id=group_id)
            integrations = [
                self._envelope_integration(
                    self._postiz.map_integration(
                        item,
                        brand_slug=req.brand_slug,
                        group_id=group_id,
                    )
                )
                for item in raw
            ]
            return SocialMediaListIntegrationsResponse(
                **work_fields_from_request(req),
                ok=True,
                request_id=req.request_id,
                integrations=integrations,
            )
        except PostizApiError as exc:
            return build_list_integrations_error(
                req,
                str(exc),
                worker_error=worker_error_from_http_provider(exc.status_code, str(exc)),
            )

    async def _handle_publish(self, payload: dict) -> SocialMediaPublishResponse:
        outcome = validate_publish_request(payload)
        if outcome.error is not None or outcome.request is None:
            return build_publish_error(
                SocialMediaPublishRequest.model_construct(
                    **coerce_envelope_fields(
                        {
                            "request_id": str(payload.get("request_id") or "unknown"),
                            "brand_slug": "unknown",
                            "content": "x",
                            "platforms": ["x"],
                        }
                    )
                ),
                error=outcome.error or "malformed request",
            )
        req = outcome.request
        assert isinstance(req, SocialMediaPublishRequest)
        return await self._publish(req, post_type="now", when=datetime.now(tz=UTC))

    async def _handle_schedule(self, payload: dict) -> SocialMediaPublishResponse:
        outcome = validate_schedule_request(payload)
        if outcome.error is not None or outcome.request is None:
            return build_publish_error(
                SocialMediaScheduleRequest.model_construct(
                    **coerce_envelope_fields(
                        {
                            "request_id": str(payload.get("request_id") or "unknown"),
                            "brand_slug": "unknown",
                            "content": "x",
                            "platforms": ["x"],
                            "publish_at": datetime.now(tz=UTC),
                        }
                    )
                ),
                error=outcome.error or "malformed request",
            )
        req = outcome.request
        assert isinstance(req, SocialMediaScheduleRequest)
        when = req.publish_at
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return await self._publish(req, post_type="schedule", when=when.astimezone(UTC))

    async def _publish(
        self,
        req: SocialMediaPublishRequest,
        *,
        post_type: str,
        when: datetime,
    ) -> SocialMediaPublishResponse:
        try:
            group_id, integrations = await self._postiz.resolve_integrations(
                brand_slug=req.brand_slug,
                platforms=list(req.platforms),
                integration_ids=req.integration_ids,
            )
            if not integrations:
                return SocialMediaPublishResponse(
                    **work_fields_from_request(req),
                    ok=False,
                    request_id=req.request_id,
                    error=f"no integrations for brand={req.brand_slug!r}",
                )
            media_items = (
                await self._upload_media(req.media) if req.media else []
            )
            body = self._postiz.build_post_payload(
                post_type=post_type,
                content=req.content,
                date=when,
                integrations=integrations,
                media_items=media_items,
                short_link=req.short_link,
                tags=req.tags,
                platform_settings=req.platform_settings,
            )
            created = await self._postiz.create_post(body)
            posts = [
                {
                    "post_id": str(item.get("postId", "")),
                    "integration_id": str(item.get("integration", "")),
                    "platform": next(
                        (
                            i.get("identifier")
                            for i in integrations
                            if i.get("id") == item.get("integration")
                        ),
                        "",
                    ),
                    "group_id": group_id,
                }
                for item in created
                if item.get("postId")
            ]
            return SocialMediaPublishResponse(
                **work_fields_from_request(req),
                ok=True,
                request_id=req.request_id,
                posts=posts,
                scheduled_for=when if post_type == "schedule" else None,
            )
        except PostizApiError as exc:
            return build_publish_error(
                req,
                error=str(exc),
                worker_error=worker_error_from_http_provider(exc.status_code, str(exc)),
                provider_body=exc.body,
            )
        except ValidationError as exc:
            return build_publish_error(req, error=str(exc))

    async def _resolve_group_id(self, brand_slug: str) -> str | None:
        from factory.adapters.socialmedia.slug import resolve_group_id

        groups = await self._postiz.list_groups()
        return resolve_group_id(groups, brand_slug)

    async def _reply_model(self, msg: Any, model: Any) -> None:
        await self.reply(msg, model.model_dump_json(by_alias=True).encode())

    def _ok_list_groups(
        self, req: SocialMediaListGroupsRequest, groups: list[dict]
    ) -> SocialMediaListGroupsResponse:
        return SocialMediaListGroupsResponse(
            **work_fields_from_request(req),
            ok=True,
            request_id=req.request_id,
            groups=groups,
        )

    def _envelope_group(self, mapped: dict) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "trace_id": "socialmedia-adapter",
            "issued_at": datetime.now(tz=UTC),
            **mapped,
        }

    def _envelope_integration(self, mapped: dict) -> dict:
        return self._envelope_group(mapped)