"""Wire-safe ``ImageResponse`` error reply builders."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.image.models import ImageResponse

from roxabi_satellite.image.errors import image_worker_error_from_legacy


def build_image_error_reply(
    *,
    trace_id: str,
    request_id: str,
    error: str,
    error_detail: str | None = None,
    job_id: str | None = None,
    worker_error: WorkerError | None = None,
) -> bytes:
    """Send-compatible error bytes for the image NATS adapter."""
    worker_err = worker_error or image_worker_error_from_legacy(error, error_detail)
    safe_trace = trace_id or "unknown"
    now = datetime.now(timezone.utc)
    job_kw: dict[str, Any] = {"job_id": job_id} if job_id is not None else {}
    if request_id:
        resp = ImageResponse(
            contract_version=CONTRACT_VERSION,
            trace_id=safe_trace,
            issued_at=now,
            request_id=request_id,
            ok=False,
            error=error,
            worker_error=worker_err,
            **job_kw,
        )
    else:
        resp = ImageResponse.model_construct(
            contract_version=CONTRACT_VERSION,
            trace_id=safe_trace,
            issued_at=now,
            request_id="",
            ok=False,
            error=error,
            worker_error=worker_err,
            **job_kw,
        )
    return resp.model_dump_json(exclude_none=True).encode()