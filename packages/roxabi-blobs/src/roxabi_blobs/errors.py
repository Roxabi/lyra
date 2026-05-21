"""Typed errors raised by `BlobStore` implementations.

Surface these to callers; never raise raw `OSError` or `sqlite3.OperationalError`
across the package boundary.
"""

from __future__ import annotations


class BlobError(Exception):
    """Base for all `roxabi_blobs` errors."""


class BlobNotFoundError(BlobError):
    """Raised by `get(store_key)` when the blob is absent on disk."""


class BlobWriteError(BlobError):
    """Raised by `put` when the OS or SQLite layer refuses the write
    (disk full, permission denied, schema corruption).
    """


class BlobConsistencyError(BlobError):
    """Raised when the FS and SQLite manifest disagree in a way the
    package cannot self-heal — e.g. `blobs` row points to a missing file.
    Future reconciler (V6) resolves these; the package surfaces them.
    """
