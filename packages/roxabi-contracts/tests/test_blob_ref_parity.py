"""Parity + conversion tests between wire BlobRef and storage BlobRef.

Two test groups:
  (a) field-set parity — wire fields == storage fields - {id, is_sentinel}
  (b) from_store_ref round-trip — classmethod converts storage→wire without
      losing created_at or optional fields (the bug we are fixing: a naive
      BlobRef(**dump) would call datetime.now() for created_at instead of
      preserving the storage value).

Design constraint: roxabi-contracts MUST NOT import roxabi-blobs at runtime
(storage ↔ transport cycle breaks satellite consumers). The storage field-set
is therefore encoded as a module-level frozenset constant below.  Keep it in
sync with roxabi_blobs.BlobRef whenever that model gains or loses fields.

Coverage oracle (one-sided):
  STORAGE_FIELDS is a hardcoded frozenset — it is NOT a live mirror of
  roxabi_blobs.BlobRef.  The parity test catches:
    (1) wire-side additions: a new field on roxabi_contracts.BlobRef not in
        STORAGE_FIELDS will fail the assertion.
    (2) STORAGE_FIELDS/wire mismatch: a field removed from wire but still in
        STORAGE_FIELDS - {id, is_sentinel} will also fail.
  It does NOT automatically catch storage-side field additions: if
  roxabi_blobs.BlobRef gains a new field, STORAGE_FIELDS must be updated
  MANUALLY in this file to re-enable detection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from roxabi_contracts import BlobRef

# ---------------------------------------------------------------------------
# (a) Storage field-set mirror — sync with roxabi_blobs.BlobRef
#
# Source of truth: packages/roxabi-blobs/src/roxabi_blobs/models.py
# As of 2026-05-30 the storage model has exactly these 11 fields:
#   store_key, content_hash, mime, size, filename, source,
#   platform_ref, platform_message_id, id, created_at, is_sentinel
#
# ¬import roxabi_blobs here: would introduce a runtime dep in the contracts
# package and create the storage↔transport cycle ADR-067 explicitly forbids.
# ---------------------------------------------------------------------------

STORAGE_FIELDS: frozenset[str] = frozenset(
    {
        "store_key",
        "content_hash",
        "mime",
        "size",
        "filename",
        "source",
        "platform_ref",
        "platform_message_id",
        "id",
        "created_at",
        "is_sentinel",
    }
)


# ---------------------------------------------------------------------------
# (a) Field-set parity test (SC3)
# ---------------------------------------------------------------------------


def test_wire_field_set_equals_storage_minus_storage_only_fields() -> None:
    """Wire BlobRef fields == storage BlobRef fields - {id, is_sentinel} (SC3).

    One-sided oracle — catches:
      (1) wire-side additions: a new wire field not in STORAGE_FIELDS fails here.
      (2) STORAGE_FIELDS/wire mismatch: a removed wire field still in
          STORAGE_FIELDS - {id, is_sentinel} fails here.
    Does NOT auto-detect storage-side additions: if roxabi_blobs.BlobRef gains a
    new field, STORAGE_FIELDS must be updated manually in this test file.
    """
    wire_fields = set(BlobRef.model_fields)
    storage_only = {"id", "is_sentinel"}

    assert wire_fields == STORAGE_FIELDS - storage_only, (
        f"Wire field-set drift detected.\n"
        f"  wire fields:    {sorted(wire_fields)}\n"
        f"  expected:       {sorted(STORAGE_FIELDS - storage_only)}\n"
        f"  in wire only:   {sorted(wire_fields - (STORAGE_FIELDS - storage_only))}\n"
        f"  in storage only:{sorted((STORAGE_FIELDS - storage_only) - wire_fields)}\n"
        "Update STORAGE_FIELDS in this test to match roxabi_blobs.BlobRef."
    )


# ---------------------------------------------------------------------------
# (b) from_store_ref round-trip (SC4)
#
# Duck-typed fake — avoids importing roxabi_blobs while exercising the full
# conversion contract.  A real roxabi_blobs.BlobRef exposes exactly this API:
#   .model_dump(exclude: set[str] | None = None) -> dict[str, Any]
# ---------------------------------------------------------------------------

_FIXED_CREATED_AT = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)


class _FakeStorageBlobRef:
    """Duck-typed stand-in for roxabi_blobs.BlobRef.

    Exposes model_dump(exclude=...) returning a storage-shaped dict that
    includes all 11 storage fields (store_key, content_hash, mime, size,
    filename, source, platform_ref, platform_message_id, id, created_at,
    is_sentinel).  The converter under test calls model_dump(exclude={"id",
    "is_sentinel"}) and passes the result to BlobRef.model_validate() — so
    extra=forbid on the wire model must see exactly the 9 wire fields.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {
            "store_key": "sha256-abc123def456",
            "content_hash": "abcdef1234567890" * 4,  # 64-char hex
            "mime": "audio/ogg",
            "size": 4096,
            "source": "telegram",
            "filename": "voice_note.ogg",
            "platform_ref": "tg:BQACAgIAAxkBAA",
            "platform_message_id": "msg-99",
            "id": 42,
            "created_at": _FIXED_CREATED_AT,
            "is_sentinel": False,
        }

    def model_dump(self, *, exclude: set[str] | None = None) -> dict[str, Any]:
        if exclude is None:
            return dict(self._data)
        return {k: v for k, v in self._data.items() if k not in exclude}


def test_from_store_ref_returns_wire_blob_ref() -> None:
    """from_store_ref(fake) returns a roxabi_contracts.BlobRef instance (SC4)."""
    fake = _FakeStorageBlobRef()
    result = BlobRef.from_store_ref(fake)  # type: ignore[attr-defined]
    assert isinstance(result, BlobRef)


def test_from_store_ref_preserves_created_at() -> None:
    """from_store_ref preserves the storage created_at — not a fresh default.

    This is the primary regression being fixed: the naive BlobRef(**dump)
    path would trigger the default_factory (datetime.now()) for created_at
    if created_at were not present in the kwargs.  from_store_ref must
    carry the original timestamp through model_validate().
    """
    fake = _FakeStorageBlobRef()
    result = BlobRef.from_store_ref(fake)  # type: ignore[attr-defined]
    assert result.created_at == _FIXED_CREATED_AT, (
        f"created_at was not preserved: got {result.created_at!r}, "
        f"expected {_FIXED_CREATED_AT!r}"
    )


def test_from_store_ref_carries_optional_fields() -> None:
    """from_store_ref carries filename, platform_ref, platform_message_id through."""
    fake = _FakeStorageBlobRef()
    result = BlobRef.from_store_ref(fake)  # type: ignore[attr-defined]
    assert result.filename == "voice_note.ogg"
    assert result.platform_ref == "tg:BQACAgIAAxkBAA"
    assert result.platform_message_id == "msg-99"


def test_extra_forbid_rejects_storage_only_fields() -> None:
    """BlobRef.model_validate raises ValidationError when storage-only fields present.

    This is the falsifiable guard for the exclude={"id", "is_sentinel"} call inside
    from_store_ref.  The test constructs the full storage dump (with id and
    is_sentinel) and passes it DIRECTLY to model_validate — i.e. without the
    exclude= filter — asserting that extra="forbid" on the wire model rejects it.

    If extra="forbid" were removed from BlobRef, this test would pass the validate
    call and the assertion below would fail, catching the regression.

    Deleting exclude= from from_store_ref (without removing extra="forbid") would
    make from_store_ref itself raise ValidationError at runtime — caught by
    test_from_store_ref_returns_wire_blob_ref above.
    """
    import pydantic

    fake = _FakeStorageBlobRef()
    full_dump = fake.model_dump()  # includes id and is_sentinel — no exclude=
    with pytest.raises(pydantic.ValidationError):
        BlobRef.model_validate(full_dump)
