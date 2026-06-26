"""BlobStore / httpx delivery error sanitization for image satellites."""

from __future__ import annotations


def sanitize_delivery_exception(exc: Exception) -> str:
    """Map an httpx / BlobStore exception to a safe diagnostic string.

    Wire-facing and log-facing — must not carry URLs, headers, or bearer tokens.
    """
    try:
        import httpx
    except ImportError:
        return "internal delivery error"

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code  # type: ignore[attr-defined]
        return f"upstream HTTP {status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "BlobStore request timed out"
    if isinstance(exc, httpx.ConnectError):
        return "BlobStore connection failed"
    if isinstance(exc, httpx.HTTPError):
        return "BlobStore transport error"
    return "internal delivery error"