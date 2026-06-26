"""Social media tool-domain NATS contract models.

Factory-facing, brand-centric requests. The satellite adapter maps these to a
backing provider REST API (v1 implementation: Postiz Public API).

Operator setup (provider UI, one-time):
  Organization  → 1 API key (HTTP ``Authorization`` header)
  Group/Customer → brand bucket (Enichu, Bully, Roxabi)
  Integration   → connected social account, assigned to a group
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from roxabi_contracts.blob_ref import BlobRef
from roxabi_contracts.envelope import ContractEnvelope, WorkEnvelope
from roxabi_contracts.errors import WorkerError

BrandSlug = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
]

SocialMediaPlatform = Literal[
    "x",
    "linkedin",
    "linkedin-page",
    "instagram",
    "instagram-standalone",
    "facebook",
    "threads",
    "youtube",
    "tiktok",
    "pinterest",
    "reddit",
    "discord",
    "slack",
    "mastodon",
    "bluesky",
    "medium",
    "wordpress",
]

SocialMediaProvider = Literal["postiz"]


class SocialMediaGroup(ContractEnvelope):
    id: Annotated[str, StringConstraints(min_length=1)]
    slug: BrandSlug
    name: str


class SocialMediaIntegration(ContractEnvelope):
    id: Annotated[str, StringConstraints(min_length=1)]
    name: str
    platform: str
    group_id: str | None = None
    group_slug: BrandSlug | None = None
    disabled: bool = False


class SocialMediaListGroupsRequest(WorkEnvelope):
    request_id: Annotated[str, StringConstraints(min_length=1)]


class SocialMediaListGroupsResponse(WorkEnvelope):
    ok: bool
    request_id: Annotated[str, StringConstraints(min_length=1)]
    groups: list[SocialMediaGroup] = Field(default_factory=list)
    error: str | None = None
    worker_error: WorkerError | None = None


class SocialMediaListIntegrationsRequest(WorkEnvelope):
    request_id: Annotated[str, StringConstraints(min_length=1)]
    brand_slug: BrandSlug | None = None


class SocialMediaListIntegrationsResponse(WorkEnvelope):
    ok: bool
    request_id: Annotated[str, StringConstraints(min_length=1)]
    integrations: list[SocialMediaIntegration] = Field(default_factory=list)
    error: str | None = None
    worker_error: WorkerError | None = None


class SocialMediaPublishRequest(WorkEnvelope):
    request_id: Annotated[str, StringConstraints(min_length=1)]
    brand_slug: BrandSlug
    content: Annotated[str, StringConstraints(min_length=1)]
    platforms: list[SocialMediaPlatform] = Field(min_length=1)
    integration_ids: list[str] | None = None
    media: list[BlobRef] = Field(default_factory=list)
    short_link: bool = False
    platform_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tags: list[dict[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integration_ids_non_empty_when_set(self) -> Self:
        if self.integration_ids is not None and len(self.integration_ids) == 0:
            raise ValueError("integration_ids must be omitted or non-empty")
        return self


class SocialMediaScheduleRequest(SocialMediaPublishRequest):
    publish_at: datetime


class SocialMediaPostRef(ContractEnvelope):
    post_id: Annotated[str, StringConstraints(min_length=1)]
    integration_id: str
    platform: str
    group_id: str | None = None


class SocialMediaPublishResponse(WorkEnvelope):
    ok: bool
    request_id: Annotated[str, StringConstraints(min_length=1)]
    posts: list[SocialMediaPostRef] = Field(default_factory=list)
    scheduled_for: datetime | None = None
    error: str | None = None
    worker_error: WorkerError | None = None
    validation_errors: list[dict[str, str]] = Field(default_factory=list)


class SocialMediaHeartbeat(ContractEnvelope):
    worker_id: Annotated[str, StringConstraints(min_length=1)]
    service: str
    host: str
    provider: SocialMediaProvider
    provider_base_url: str
    ts: float
    active_requests: int | None = None