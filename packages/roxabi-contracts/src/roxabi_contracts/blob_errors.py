"""Port-level blob errors (ADR-082)."""

from __future__ import annotations

__all__ = ["BlobNotFoundError", "BlobStoreServerError"]


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


class BlobStoreServerError(Exception):
    """Raised by BlobStorePort.put when the backing store returns an HTTP 5xx.

    Callers import this from ``roxabi_contracts`` (not ``roxabi_blobs`` or
    ``httpx``); the adapter layer translates the HTTP error at the seam.
    Infrastructure is the only layer permitted to inspect HTTP status codes.

    ``.status_code`` carries the HTTP status (e.g. 500, 502, 503) when
    available; None when the error predates HTTP (e.g. connection failure
    surfaced as a 5xx-family escalation). ``.message`` is a human-readable
    description for logging.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"{message}{suffix}")
