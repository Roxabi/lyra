"""Social media tool-domain NATS contract surface.

Factory agents invoke ``factory.tool.socialmedia.*`` subjects; the satellite
adapter translates to a backing provider HTTP API (v1: Postiz ``/public/v1``).

Operator setup (org, groups, OAuth channels) lives in the provider UI — not on
this wire. See ``MAPPING.md`` for the Postiz v1 mapping.

The ``fixtures`` submodule is test-only — import explicitly, not re-exported.
"""

from roxabi_contracts.socialmedia.models import (
    SocialMediaGroup,
    SocialMediaHeartbeat,
    SocialMediaIntegration,
    SocialMediaListGroupsRequest,
    SocialMediaListGroupsResponse,
    SocialMediaListIntegrationsRequest,
    SocialMediaListIntegrationsResponse,
    SocialMediaPostRef,
    SocialMediaPublishRequest,
    SocialMediaPublishResponse,
    SocialMediaScheduleRequest,
)
from roxabi_contracts.socialmedia.subjects import (
    SUBJECTS,
    per_worker_socialmedia,
    validate_worker_id,
)

__all__ = [
    "SUBJECTS",
    "SocialMediaGroup",
    "SocialMediaHeartbeat",
    "SocialMediaIntegration",
    "SocialMediaListGroupsRequest",
    "SocialMediaListGroupsResponse",
    "SocialMediaListIntegrationsRequest",
    "SocialMediaListIntegrationsResponse",
    "SocialMediaPostRef",
    "SocialMediaPublishRequest",
    "SocialMediaPublishResponse",
    "SocialMediaScheduleRequest",
    "per_worker_socialmedia",
    "validate_worker_id",
]