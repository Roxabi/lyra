"""Wire-contract invariant tests for BlobRef and PENDING_STORE_KEY.

Covers: round-trip serialisation, frozen model, extra="forbid", sentinel
constant value, top-level re-export, sentinel dispatch idiom, and UTC-aware
default for created_at.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from roxabi_contracts import PENDING_STORE_KEY, BlobRef

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
# 4. PENDING_STORE_KEY constant value
# ---------------------------------------------------------------------------


def test_pending_store_key_value() -> None:
    """PENDING_STORE_KEY must equal the literal '__pending__'."""
    assert PENDING_STORE_KEY == "__pending__"


# ---------------------------------------------------------------------------
# 5. Top-level import re-export (exercises T2)
#    Import is at module level above — collection failure = T2 not landed yet.
#    If that happens, fall back to:
#      from roxabi_contracts.blob_ref import BlobRef, PENDING_STORE_KEY
#    and add comment "switch to top-level import once T2 lands".
# ---------------------------------------------------------------------------


def test_top_level_import_exposes_blob_ref_and_pending_store_key() -> None:
    """BlobRef and PENDING_STORE_KEY are accessible from the top-level package."""
    # Both were imported at module level — reaching this line confirms it.
    assert BlobRef is not None
    assert PENDING_STORE_KEY is not None


# ---------------------------------------------------------------------------
# 6. Sentinel comparison idiom
# ---------------------------------------------------------------------------


def test_sentinel_dispatch_idiom() -> None:
    """Worker dispatch pattern: store_key == PENDING_STORE_KEY flags legacy path."""
    br = BlobRef(
        store_key=PENDING_STORE_KEY,
        content_hash="",
        mime="audio/ogg",
        size=0,
        source="telegram",
        platform_ref="tg-file-id-xyz",
    )
    assert br.store_key == PENDING_STORE_KEY


# ---------------------------------------------------------------------------
# 7. created_at default is UTC-aware
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
# 8. Required-field rejection — spec SC-1
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
# 9. content_hash empty only valid when store_key == sentinel
# ---------------------------------------------------------------------------


def test_empty_content_hash_rejected_when_store_key_is_real() -> None:
    """Worker integrity-check invariant: empty content_hash only OK on sentinel path."""
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


def test_empty_content_hash_allowed_on_sentinel_path() -> None:
    """Adapters emit content_hash='' + store_key=PENDING_STORE_KEY — must validate."""
    br = BlobRef.model_validate(
        {
            "store_key": PENDING_STORE_KEY,
            "content_hash": "",
            "mime": "audio/ogg",
            "size": 0,
            "source": "telegram",
        }
    )
    assert br.content_hash == ""
