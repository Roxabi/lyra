"""Port-level blob errors (ADR-082)."""

from __future__ import annotations

__all__ = ["BlobNotFoundError"]


def _redact_key(key: str) -> str:
    """Display form of an opaque store_key — short prefix + length, never full key."""
    return key if len(key) <= 12 else f"{key[:8]}…({len(key)} chars)"


class BlobNotFoundError(Exception):
    """Raised by BlobStorePort.get when an opaque store_key is absent.

    Callers import this from ``roxabi_contracts`` (not ``roxabi_blobs``); the
    adapter layer translates the storage-layer error at the seam.

    ``.key`` is the RAW opaque store_key (content-addressed, NOT a filesystem
    path) — provided for programmatic retry/diagnostics. ``str(err)`` is a
    redacted display form so accidental logging cannot dump a full key.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(_redact_key(key))
