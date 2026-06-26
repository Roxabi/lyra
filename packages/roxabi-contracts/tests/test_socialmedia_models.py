"""Roundtrip tests for roxabi_contracts.socialmedia models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from roxabi_contracts.blob_ref import BlobRef
from roxabi_contracts.socialmedia import (
    SocialMediaListGroupsRequest,
    SocialMediaPublishRequest,
    SocialMediaScheduleRequest,
)

_ENVELOPE: dict = {
    "contract_version": "1",
    "trace_id": "tst-trace",
    "issued_at": datetime(2026, 6, 26, tzinfo=timezone.utc),
    "job_id": "job-abc123",
}


def test_publish_request_roundtrip() -> None:
    req = SocialMediaPublishRequest(
        **_ENVELOPE,
        request_id="r1",
        brand_slug="enichu",
        content="Hello",
        platforms=["x", "linkedin"],
    )
    assert req.brand_slug == "enichu"


def test_schedule_requires_publish_at() -> None:
    with pytest.raises(ValidationError):
        SocialMediaScheduleRequest(
            **_ENVELOPE,
            request_id="r2",
            brand_slug="bully",
            content="Later",
            platforms=["x"],
        )


def test_list_groups_minimal() -> None:
    req = SocialMediaListGroupsRequest(**_ENVELOPE, request_id="g1")
    assert req.request_id == "g1"


def test_publish_with_media() -> None:
    blob = BlobRef(
        store_key="k1",
        content_hash="abc",
        mime="image/png",
        size=100,
        source="factory",
    )
    req = SocialMediaPublishRequest(
        **_ENVELOPE,
        request_id="r4",
        brand_slug="enichu",
        content="With image",
        platforms=["x"],
        media=[blob],
    )
    assert req.media[0].store_key == "k1"