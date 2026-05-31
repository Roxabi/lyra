"""Content-addressed blob pointer embedded in NATS wire contracts.

BlobRef replaces inline-bytes fields (audio_b64, audio_bytes, image_b64,
file_path) on the wire. It is a frozen primitive value type — not a wire
envelope — designed to be embedded inside ContractEnvelope subclasses.

See ADR-067.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BlobRef(BaseModel):
    """Content-addressed pointer to a binary blob in the Roxabi BlobStore.

    Replaces inline-bytes fields (audio_b64, audio_bytes, image_b64, file_path)
    on the wire. The store_key is the content-addressed primary key in the
    BlobStore SQLite manifest; content_hash is the SHA-256 of the bytes and
    enables idempotent put + dedup. See ADR-067.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    store_key: str
    """BlobStore PK — opaque server-issued string."""

    content_hash: str
    """SHA-256 hex of payload bytes."""

    mime: str
    """MIME type (e.g. "audio/ogg", "image/png")."""

    size: int
    """Byte length of the blob."""

    source: str
    """Short producer identifier ("telegram", "discord", "voicecli", "imagecli")."""

    filename: str | None = None
    """Optional original filename."""

    platform_ref: str | None = None
    """Optional platform-native handle (Telegram file_id, Discord attachment URL)."""

    platform_message_id: str | None = None
    """Optional message-id correlation key."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    """UTC-aware creation timestamp."""

    @field_validator("content_hash")
    @classmethod
    def _require_non_empty_content_hash(cls, v: str) -> str:
        if not v:
            raise ValueError("content_hash must be non-empty")
        return v

    @classmethod
    def from_store_ref(cls, store_ref: Any) -> "BlobRef":
        """Canonical storage→wire converter. Duck-typed: accepts any object with
        model_dump() (e.g. roxabi_blobs.BlobRef) without importing it (no
        storage↔transport cycle). Drops storage-only fields {id, is_sentinel};
        every other field (incl. created_at) is carried through verbatim.

        A ``pydantic.ValidationError`` from ``model_validate`` is INTENTIONAL —
        it signals field-set drift between the storage and wire schemas (the
        parity test is the primary early-warning gate).  Callers MUST NOT
        swallow it; let it propagate so the mismatch is surfaced immediately.
        """
        return cls.model_validate(store_ref.model_dump(exclude={"id", "is_sentinel"}))
