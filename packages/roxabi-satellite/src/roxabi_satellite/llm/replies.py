"""Wire-safe LLM error reply builders (streaming + blocking)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.llm.models import LlmChunkEvent, LlmResponse

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def build_llm_error_reply(payload: dict, worker_error: WorkerError, *, stream: bool) -> bytes:  # noqa: E501
    """Build and serialize an error envelope (``LlmChunkEvent`` or ``LlmResponse``)."""
    rid = str(payload.get("request_id", "unknown"))[:128]
    safe_id = rid if _REQUEST_ID_RE.match(rid) else "unknown"
    trace_id = payload.get("trace_id") or safe_id
    job_id_kwargs: dict[str, Any] = {}
    if job_id := payload.get("job_id"):
        job_id_kwargs["job_id"] = job_id
    if stream:
        return (
            LlmChunkEvent(
                contract_version=CONTRACT_VERSION,
                trace_id=trace_id,
                issued_at=datetime.now(timezone.utc),
                request_id=safe_id,
                done=True,
                is_error=True,
                error=worker_error.message,
                worker_error=worker_error,
                **job_id_kwargs,
            )
            .model_dump_json(exclude_none=True)
            .encode()
        )
    return (
        LlmResponse(
            contract_version=CONTRACT_VERSION,
            trace_id=trace_id,
            issued_at=datetime.now(timezone.utc),
            request_id=safe_id,
            ok=False,
            error=worker_error.message,
            worker_error=worker_error,
            **job_id_kwargs,
        )
        .model_dump_json(exclude_none=True)
        .encode()
    )