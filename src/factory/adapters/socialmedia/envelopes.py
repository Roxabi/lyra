"""Contract envelope builders for SocialMediaNatsAdapter responses."""

from __future__ import annotations

from datetime import UTC, datetime

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.socialmedia.models import (
    SocialMediaGroup,
    SocialMediaIntegration,
    SocialMediaPostRef,
)


def envelope_fields(mapped: dict) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "trace_id": "socialmedia-adapter",
        "issued_at": datetime.now(tz=UTC),
        **mapped,
    }


def envelope_group(mapped: dict) -> SocialMediaGroup:
    return SocialMediaGroup.model_validate(envelope_fields(mapped))


def envelope_integration(mapped: dict) -> SocialMediaIntegration:
    return SocialMediaIntegration.model_validate(envelope_fields(mapped))


def envelope_post(mapped: dict) -> SocialMediaPostRef:
    return SocialMediaPostRef.model_validate(envelope_fields(mapped))