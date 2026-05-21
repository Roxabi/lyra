"""Pydantic envelope returned by `BlobStore` operations.

Mirrors ADR-067 §BlobRef. V2 (#1064) adds a wire-side mirror in
`roxabi-contracts`; the two are kept in sync by spec, not by import
(avoids storage ↔ transport cycle).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BlobRef(BaseModel):
    """Reference to a content-addressed blob.

    `store_key` is the opaque handle returned by `BlobStore.put` and
    consumed by `BlobStore.get`. For the FS impl it is the absolute path;
    for a future S3/MinIO impl it would be the `s3://bucket/key` URI.
    Callers MUST treat it as opaque.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    store_key: str = Field(
        description="Opaque handle for BlobStore.get — do not parse."
    )
    content_hash: str = Field(description="sha256(data) as lower-case hex.")
    mime: str
    size: int = Field(ge=0)
    filename: str | None = None
    source: str = Field(
        description="Ingestion source: 'telegram', 'discord', 'tts', 'stt', ..."
    )
    platform_ref: str | None = Field(
        default=None,
        description=(
            "Recovery handle on source platform "
            "(e.g. 'tg:<file_id>', 'discord:<ch>/<msg>/<att>')."
        ),
    )
    platform_message_id: str | None = Field(
        default=None,
        description="Distinct from platform_ref; reconstructs the conversation thread.",
    )
    created_at: datetime = Field(description="Provenance: when this ref was ingested.")

    @field_validator("created_at")
    @classmethod
    def _ensure_tz_aware(cls, v: datetime) -> datetime:
        """Reject naive datetimes — callers downstream rely on `astimezone(...)`."""
        if v.tzinfo is None:
            raise ValueError(
                "BlobRef.created_at must be timezone-aware (got naive datetime)"
            )
        return v
