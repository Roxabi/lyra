"""Content-addressed blob pointer embedded in NATS wire contracts.

BlobRef replaces inline-bytes fields (audio_b64, audio_bytes, image_b64,
file_path) on the wire. It is a frozen primitive value type — not a wire
envelope — designed to be embedded inside ContractEnvelope subclasses.

See ADR-067.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

PENDING_STORE_KEY = "__pending__"
"""Sentinel store_key emitted by adapters before BlobStore ingest lands.

Workers receiving a BlobRef where store_key == PENDING_STORE_KEY MUST fall back
to platform_ref (legacy fetch path) instead of calling blob_store.get(store_key).
Removed once adapter eager-ingest (epic #1061 slices V3/V4) is in production.
"""


class BlobRef(BaseModel):
    """Content-addressed pointer to a binary blob in the Roxabi BlobStore.

    Replaces inline-bytes fields (audio_b64, audio_bytes, image_b64, file_path)
    on the wire. The store_key is the content-addressed primary key in the
    BlobStore SQLite manifest; content_hash is the SHA-256 of the bytes and
    enables idempotent put + dedup. See ADR-067.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    store_key: str
    """BlobStore PK. Equals PENDING_STORE_KEY during adapter transition."""

    content_hash: str
    """SHA-256 hex of payload bytes. May be "" when store_key == PENDING_STORE_KEY."""

    mime: str
    """MIME type (e.g. "audio/ogg", "image/png")."""

    size: int
    """Byte length of the blob."""

    source: str
    """Short producer identifier ("telegram", "discord", "voicecli", "imagecli")."""

    filename: str | None = None
    """Optional original filename."""

    platform_ref: str | None = None
    """Optional platform-native handle (Telegram file_id, Discord attachment URL).

    Used by workers on the sentinel path (store_key == PENDING_STORE_KEY) to
    fetch the blob via the legacy platform API.
    """

    platform_message_id: str | None = None
    """Optional message-id correlation key."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    """UTC-aware creation timestamp."""
