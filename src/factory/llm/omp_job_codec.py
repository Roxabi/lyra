"""OmpJobCodec — decode JobResult → LlmResult (ADR-089 S4)."""

from __future__ import annotations

import logging

from factory.core.ports.llm import LlmResult
from roxabi_contracts.errors import KNOWN_CODES, WorkerError
from roxabi_contracts.jobs import JobResult

log = logging.getLogger(__name__)

_FALLBACK_CODE = "worker.internal"
_MALFORMED_ERROR = "omp returned a malformed result"
_TIMEOUT_ERROR = "omp request timed out"


def _validate_worker_error(we: WorkerError | None) -> WorkerError | None:
    if we is None or we.code in KNOWN_CODES:
        return we
    log.warning(
        "OmpJobCodec: unregistered WorkerError code %r — mapping to %r",
        we.code,
        _FALLBACK_CODE,
    )
    return WorkerError(
        code=_FALLBACK_CODE,
        message=we.message,
        retryable=we.retryable,
        detail=we.detail,
    )


class OmpJobCodec:
    """Decode omp worker JobResult envelopes into hub-side LlmResult."""

    def decode(self, result: JobResult) -> LlmResult:
        if result.status == "success":
            return LlmResult(result=(result.data or {}).get("result", ""))

        validated = _validate_worker_error(result.error)
        error_msg = validated.message if validated else _MALFORMED_ERROR
        return LlmResult(
            error=error_msg,
            retryable=validated.retryable if validated else True,
            worker_error=validated,
        )

    def decode_malformed(self) -> LlmResult:
        return LlmResult(error=_MALFORMED_ERROR, retryable=False)

    def decode_timeout(self) -> LlmResult:
        worker_error = WorkerError(
            code="transport.timeout",
            message=_TIMEOUT_ERROR,
            retryable=True,
        )
        return LlmResult(
            error=_TIMEOUT_ERROR,
            retryable=True,
            worker_error=worker_error,
        )