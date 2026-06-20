"""Job envelope serialization and exception classification for omp RpcBridge."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.jobs.models import JobProgress, JobResult
from roxabi_contracts.jobs.subjects import jobs_result


def classify_exception(exc: BaseException) -> WorkerError:
    """Map an exception to a WorkerError.

    SanitizedError discipline (ADR-073): message field = type(exc).__name__ only.
    """
    name = type(exc).__name__
    if name == "DigestMismatchError":
        return WorkerError(
            code="transport.contract_mismatch", message=name, retryable=False
        )
    if isinstance(exc, asyncio.TimeoutError):
        return WorkerError(code="transport.timeout", message=name, retryable=True)
    if name in ("ConnectionRefusedError", "BrokenPipeError"):
        return WorkerError(code="transport.error", message=name, retryable=True)
    return WorkerError(code="worker.internal", message=name, retryable=False)


def make_progress(job_id: str, **kwargs: Any) -> bytes:
    """Serialise a JobProgress to JSON bytes."""
    event = JobProgress(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid.uuid4()),
        issued_at=datetime.now(timezone.utc),
        job_id=job_id,
        **kwargs,
    )
    return event.model_dump_json().encode()


def make_result(job_id: str, **kwargs: Any) -> bytes:
    """Serialise a JobResult to JSON bytes."""
    event = JobResult(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid.uuid4()),
        issued_at=datetime.now(timezone.utc),
        job_id=job_id,
        **kwargs,
    )
    return event.model_dump_json().encode()


async def publish_job_error(nc: Any, job_id: str, exc: BaseException) -> None:
    """Publish a JobResult(status=error) for a failed job without requiring a bridge."""
    if nc is None:
        return
    worker_error = classify_exception(exc)
    payload = make_result(job_id, status="error", error=worker_error)
    await nc.publish(jobs_result(job_id), payload)
