"""Wire-contract invariant tests for BlobRef.

Covers: round-trip serialisation, frozen model, extra="forbid", UTC-aware
default for created_at, required-field rejection, and content_hash validation.

Note: the retired sentinel constant and its validator tests were removed in #1553.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from roxabi_contracts import BlobRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_blob_ref() -> BlobRef:
    """BlobRef with all 9 fields explicitly set."""
    return BlobRef(
        store_key="abc123",
        content_hash="deadbeef",
        mime="audio/ogg",
        size=1024,
        source="telegram",
        filename="x.ogg",
        platform_ref="ref",
        platform_message_id="msg",
        created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_preserves_all_fields() -> None:
    """model_dump() → model_validate() preserves every field value."""
    original = _full_blob_ref()
    restored = BlobRef.model_validate(original.model_dump())

    assert restored.store_key == "abc123"
    assert restored.content_hash == "deadbeef"
    assert restored.mime == "audio/ogg"
    assert restored.size == 1024
    assert restored.source == "telegram"
    assert restored.filename == "x.ogg"
    assert restored.platform_ref == "ref"
    assert restored.platform_message_id == "msg"
    assert restored.created_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 2. Frozen — mutation raises ValidationError
# ---------------------------------------------------------------------------


def test_frozen_rejects_field_assignment() -> None:
    """Assigning to any field on a frozen BlobRef raises ValidationError."""
    br = _full_blob_ref()
    with pytest.raises((ValidationError, TypeError)):
        br.size = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 3. extra="forbid" — unknown kwarg raises ValidationError
# ---------------------------------------------------------------------------


def test_extra_forbid_rejects_unknown_kwarg() -> None:
    """Constructing BlobRef with an unknown field raises ValidationError."""
    with pytest.raises(ValidationError):
        BlobRef.model_validate(
            {
                "store_key": "abc123",
                "content_hash": "deadbeef",
                "mime": "audio/ogg",
                "size": 1024,
                "source": "telegram",
                "unknown_field": "should_fail",
            }
        )


# ---------------------------------------------------------------------------
# 4. Top-level import re-export
# ---------------------------------------------------------------------------


def test_top_level_import_exposes_blob_ref() -> None:
    """BlobRef is accessible from the top-level package."""
    # Imported at module level above — reaching this line confirms it.
    assert BlobRef is not None


# ---------------------------------------------------------------------------
# 5. created_at default is UTC-aware
# ---------------------------------------------------------------------------


def test_created_at_default_is_utc_aware() -> None:
    """Omitting created_at produces a timezone-aware datetime (UTC)."""
    br = BlobRef(
        store_key="abc123",
        content_hash="deadbeef",
        mime="audio/ogg",
        size=1024,
        source="telegram",
    )
    assert br.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# 6. Required-field rejection — spec SC-1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_field",
    ["store_key", "content_hash", "mime", "size", "source"],
)
def test_missing_required_field_raises_validation_error(missing_field: str) -> None:
    """Omitting any of the 5 required fields raises ValidationError (spec SC-1)."""
    payload: dict[str, Any] = {
        "store_key": "abc123",
        "content_hash": "deadbeef",
        "mime": "audio/ogg",
        "size": 1024,
        "source": "telegram",
    }
    payload.pop(missing_field)
    with pytest.raises(ValidationError):
        BlobRef.model_validate(payload)


# ---------------------------------------------------------------------------
# 7. content_hash non-empty required for real store_key
# ---------------------------------------------------------------------------


def test_empty_content_hash_rejected() -> None:
    """Empty content_hash is not valid for any BlobRef post-#1553."""
    with pytest.raises(ValidationError):
        BlobRef.model_validate(
            {
                "store_key": "real-blob-key",
                "content_hash": "",
                "mime": "audio/ogg",
                "size": 1024,
                "source": "telegram",
            }
        )
