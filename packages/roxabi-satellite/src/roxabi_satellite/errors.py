"""WorkerError registries shared across satellite domains."""

from __future__ import annotations

from roxabi_contracts.errors import WorkerError

VOICE_VALIDATION_ERRORS: dict[str, WorkerError] = {
    "malformed_request": WorkerError(
        code="malformed_request",
        message="Request payload is malformed or missing required fields",
        retryable=False,
    ),
    "engine_unavailable": WorkerError(
        code="engine_unavailable",
        message="Requested TTS engine is not available",
        retryable=True,
    ),
    "capacity_exceeded": WorkerError(
        code="capacity_exceeded",
        message="Worker is at capacity; retry later",
        retryable=True,
    ),
}

VOICE_STT_RUNNER_ERRORS: dict[str, WorkerError] = {
    "payload_too_large": WorkerError(
        code="payload_too_large",
        message="Audio payload exceeds maximum size",
        retryable=False,
    ),
    "blobstore_not_configured": WorkerError(
        code="blobstore_not_configured",
        message="BlobStore not configured",
        retryable=False,
    ),
    "audio_fetch_failed": WorkerError(
        code="audio_fetch_failed",
        message="Failed to fetch audio from BlobStore",
        retryable=True,
    ),
    "model_load_failed": WorkerError(
        code="model_load_failed",
        message="STT model failed to load",
        retryable=True,
    ),
    "param_validation_failed": WorkerError(
        code="param_validation_failed",
        message="STT parameter validation failed",
        retryable=False,
    ),
    "transcription_failed": WorkerError(
        code="transcription_failed",
        message="Transcription failed",
        retryable=True,
    ),
}


def resolve_worker_error(
    code_or_error: str | WorkerError,
    registry: dict[str, WorkerError],
) -> WorkerError:
    if isinstance(code_or_error, WorkerError):
        return code_or_error
    return registry.get(
        code_or_error,
        WorkerError(
            code=code_or_error,
            message=code_or_error.replace("_", " ").capitalize(),
            retryable=False,
        ),
    )