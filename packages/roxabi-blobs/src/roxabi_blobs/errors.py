"""Typed errors raised by `BlobStore` implementations.

Surface these to callers; never raise raw `OSError` or `sqlite3.OperationalError`
across the package boundary.
"""

from __future__ import annotations


class BlobError(Exception):
    """Base for all `roxabi_blobs` errors."""


class BlobNotFoundError(BlobError):
    """Raised by `get(store_key)` when the blob is absent on disk OR when the
    resolved `store_key` falls outside the store root (path-traversal guard).
    The two cases share a static message — callers cannot distinguish them.
    """


class BlobWriteError(BlobError):
    """Raised by `put` / `delete` when the OS or SQLite layer refuses a write
    (disk full, permission denied, schema corruption).
    """


class BlobConsistencyError(BlobError):
    """Raised when the FS and SQLite manifest disagree in a way the package
    cannot self-heal — or when a manifest read fails. Future reconciler (V6)
    resolves these.
    """


class BlobStateError(BlobError):
    """Raised when a `BlobStore` method is called outside its `async with`
    lifecycle — distinct from `BlobWriteError` so read/write paths surface
    the same lifecycle violation under one type.
    """
