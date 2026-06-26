"""SocialMediaNatsAdapter — NATS satellite for factory.tool.socialmedia.*."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from factory.adapters.socialmedia.postiz_client import PostizApiError, PostizPublicApiClient

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.errors import WorkerError
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

log = logging.getLogger(__name__)

_QUEUE_GROUP = "socialmedia-workers"
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
            queue_group=_QUEUE_GROUP,
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
        req = SocialMediaListGroupsRequest.model_validate(payload)
        try:
            groups = [
                self._envelope_group(self._postiz.map_group(g))
                for g in await self._postiz.list_groups()
            ]
            return self._ok_list_groups(req, groups)
        except PostizApiError as exc:
            return self._err_list_groups(req, exc)

    async def _handle_list_integrations(
        self, payload: dict
    ) -> SocialMediaListIntegrationsResponse:
        req = SocialMediaListIntegrationsRequest.model_validate(payload)
        try:
            group_id = None
            if req.brand_slug:
                group_id = await self._resolve_group_id(req.brand_slug)
                if group_id is None:
                    return SocialMediaListIntegrationsResponse(
                        **self._work_fields(req),
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
                **self._work_fields(req),
                ok=True,
                request_id=req.request_id,
                integrations=integrations,
            )
        except PostizApiError as exc:
            return self._err_list_integrations(req, exc)

    async def _handle_publish(self, payload: dict) -> SocialMediaPublishResponse:
        req = SocialMediaPublishRequest.model_validate(payload)
        return await self._publish(req, post_type="now", when=datetime.now(tz=UTC))

    async def _handle_schedule(self, payload: dict) -> SocialMediaPublishResponse:
        req = SocialMediaScheduleRequest.model_validate(payload)
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
                    **self._work_fields(req),
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
                **self._work_fields(req),
                ok=True,
                request_id=req.request_id,
                posts=posts,
                scheduled_for=when if post_type == "schedule" else None,
            )
        except PostizApiError as exc:
            validation_errors = []
            if isinstance(exc.body, dict) and exc.body.get("provider"):
                validation_errors.append(
                    {
                        "provider": str(exc.body.get("provider")),
                        "error": str(exc.body.get("error") or exc),
                    }
                )
            worker_error = None
            if exc.status_code == 401:
                worker_error = WorkerError(
                    code="provider.auth",
                    message=str(exc),
                    retryable=False,
                )
            elif exc.status_code == 429:
                worker_error = WorkerError(
                    code="provider.rate_limit",
                    message=str(exc),
                    retryable=True,
                )
            return SocialMediaPublishResponse(
                **self._work_fields(req),
                ok=False,
                request_id=req.request_id,
                error=str(exc),
                worker_error=worker_error,
                validation_errors=validation_errors,
            )
        except ValidationError as exc:
            return SocialMediaPublishResponse(
                **self._work_fields(req),
                ok=False,
                request_id=req.request_id,
                error=str(exc),
            )

    async def _resolve_group_id(self, brand_slug: str) -> str | None:
        from factory.adapters.socialmedia.slug import resolve_group_id

        groups = await self._postiz.list_groups()
        return resolve_group_id(groups, brand_slug)

    async def _reply_model(self, msg: Any, model: Any) -> None:
        await self.reply(msg, model.model_dump_json(by_alias=True).encode())

    @staticmethod
    def _work_fields(req: Any) -> dict:
        return {
            "contract_version": req.contract_version,
            "trace_id": req.trace_id,
            "issued_at": req.issued_at,
            "job_id": req.job_id,
        }

    def _envelope_group(self, mapped: dict) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "trace_id": "socialmedia-adapter",
            "issued_at": datetime.now(tz=UTC),
            **mapped,
        }

    def _envelope_integration(self, mapped: dict) -> dict:
        return self._envelope_group(mapped)

    def _ok_list_groups(
        self, req: SocialMediaListGroupsRequest, groups: list[dict]
    ) -> SocialMediaListGroupsResponse:
        return SocialMediaListGroupsResponse(
            **self._work_fields(req),
            ok=True,
            request_id=req.request_id,
            groups=groups,
        )

    def _err_list_groups(
        self, req: SocialMediaListGroupsRequest, exc: PostizApiError
    ) -> SocialMediaListGroupsResponse:
        return SocialMediaListGroupsResponse(
            **self._work_fields(req),
            ok=False,
            request_id=req.request_id,
            error=str(exc),
        )

    def _err_list_integrations(
        self, req: SocialMediaListIntegrationsRequest, exc: PostizApiError
    ) -> SocialMediaListIntegrationsResponse:
        return SocialMediaListIntegrationsResponse(
            **self._work_fields(req),
            ok=False,
            request_id=req.request_id,
            error=str(exc),
        )