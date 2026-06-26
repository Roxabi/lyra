"""Provider HTTP error → ``WorkerError`` mapping for social media satellites."""

from __future__ import annotations

from roxabi_contracts.errors import WorkerError


def worker_error_from_http_provider(status_code: int | None, message: str) -> WorkerError | None:  # noqa: E501
    """Map a backing provider HTTP status to a structured ``WorkerError``."""
    if status_code is None:
        return None
    if status_code == 401:
        return WorkerError(code="provider.auth", message=message, retryable=False)
    if status_code == 429:
        return WorkerError(code="provider.rate_limit", message=message, retryable=True)
    return None