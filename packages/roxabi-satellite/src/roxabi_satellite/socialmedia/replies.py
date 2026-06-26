"""Wire-safe social media error response builders."""

from __future__ import annotations

from typing import Any

from roxabi_contracts.errors import WorkerError
from roxabi_contracts.socialmedia.models import (
    SocialMediaListGroupsResponse,
    SocialMediaListIntegrationsResponse,
    SocialMediaPublishResponse,
)

from roxabi_satellite.envelope import work_fields_from_request


def _validation_errors_from_body(body: object, fallback: str) -> list[dict[str, str]]:
    if isinstance(body, dict) and body.get("provider"):
        return [
            {
                "provider": str(body.get("provider")),
                "error": str(body.get("error") or fallback),
            }
        ]
    return []


def build_list_groups_error(
    req: Any,
    error: str,
    *,
    worker_error: WorkerError | None = None,
) -> SocialMediaListGroupsResponse:
    return SocialMediaListGroupsResponse(
        **work_fields_from_request(req),
        ok=False,
        request_id=req.request_id,
        error=error,
        worker_error=worker_error,
    )


def build_list_integrations_error(
    req: Any,
    error: str,
    *,
    worker_error: WorkerError | None = None,
) -> SocialMediaListIntegrationsResponse:
    return SocialMediaListIntegrationsResponse(
        **work_fields_from_request(req),
        ok=False,
        request_id=req.request_id,
        error=error,
        worker_error=worker_error,
    )


def build_publish_error(
    req: Any,
    *,
    error: str,
    worker_error: WorkerError | None = None,
    provider_body: object | None = None,
) -> SocialMediaPublishResponse:
    return SocialMediaPublishResponse(
        **work_fields_from_request(req),
        ok=False,
        request_id=req.request_id,
        error=error,
        worker_error=worker_error,
        validation_errors=_validation_errors_from_body(provider_body, error),
    )