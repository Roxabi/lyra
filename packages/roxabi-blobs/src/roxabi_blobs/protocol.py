"""`BlobStore` Protocol — uniform interface for content-addressed blob storage.

Implementations: `FsBlobStore` (v1, single-host). Future: MinIO/S3 behind
the same shape (multi-host).

See ADR-067 §Interface for the canonical signatures.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from roxabi_blobs.models import BlobRef


@runtime_checkable
class BlobStore(Protocol):
    """Content-addressed blob storage.

    All methods are async. Implementations MAY require an async context
    manager (`async with`) for connection lifecycle.
    """

    async def put(  # noqa: PLR0913 — signature locked by ADR-067 §Interface
        self,
        data: bytes,
        *,
        mime: str,
        source: str,
        filename: str | None = None,
        platform_ref: str | None = None,
        platform_message_id: str | None = None,
    ) -> BlobRef:
        """Store `data` content-addressed; deduplicate by sha256.

        Always inserts a new `blob_refs` row (per-ingestion provenance).
        The blob file is only written on the first sighting of the hash.
        """
        ...

    async def get(self, store_key: str) -> bytes:
        """Read the bytes referenced by `store_key`.

        Raises `BlobNotFoundError` if the blob is absent on disk.
        """
        ...

    async def exists(self, content_hash: str) -> BlobRef | None:
        """Return the latest `BlobRef` for `content_hash`, or `None`.

        "Latest" is deterministic: `ORDER BY ingested_at DESC LIMIT 1`.
        """
        ...

    async def delete(self, blob_ref_id: int) -> None:
        """Remove the `blob_refs` row identified by `blob_ref_id`.

        If no `blob_refs` rows remain for the underlying `content_hash`,
        also drops the `blobs` row and unlinks the file. Designed in v1
        but not called from production paths.
        """
        ...
