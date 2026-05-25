"""Content-addressed BlobStore — Flat-FS + SQLite manifest.

See ADR-067 (`docs/architecture/adr/067-blobstore-abstraction-flat-fs-content-addressed.mdx`)
and the V1 spec (`artifacts/specs/1063-roxabi-blobs-package-spec.mdx`).
"""  # noqa: E501 — ADR path is canonical, do not abbreviate

from __future__ import annotations

from roxabi_blobs.errors import (
    BlobConsistencyError,
    BlobError,
    BlobNotFoundError,
    BlobStateError,
    BlobWriteError,
)
from roxabi_blobs.fs_store import FsBlobStore
from roxabi_blobs.ingest import ingest_bytes_to_blob_ref
from roxabi_blobs.models import BlobRef
from roxabi_blobs.protocol import BlobStore

__all__ = [
    "BlobConsistencyError",
    "BlobError",
    "BlobNotFoundError",
    "BlobRef",
    "BlobStateError",
    "BlobStore",
    "BlobWriteError",
    "FsBlobStore",
    "ingest_bytes_to_blob_ref",
]
