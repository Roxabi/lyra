"""Legacy image adapter error codes → structured ``WorkerError``."""

from __future__ import annotations

from roxabi_contracts.errors import WorkerError

_LEGACY_ERROR_MAP: dict[str, tuple[str, bool]] = {
    "missing_required_field": ("worker.validation", False),
    "unknown_engine": ("worker.validation", False),
    "engine_load_failed": ("image.engine_unavailable", True),
    "insufficient_resources": ("worker.capacity", True),
    "generation_failed": ("worker.crash", True),
    "delivery_failed": ("worker.internal", True),
}


def image_worker_error_from_legacy(code: str, detail: str | None = None) -> WorkerError:
    """Build a structured ``WorkerError`` from a legacy free-text error code."""
    canonical, retryable = _LEGACY_ERROR_MAP.get(code, ("worker.internal", True))
    return WorkerError(code=canonical, message=code, retryable=retryable, detail=detail)