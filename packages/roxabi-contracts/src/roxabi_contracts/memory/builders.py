"""Builders for memory-domain contract models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from roxabi_contracts.envelope import CONTRACT_VERSION, new_job_id
from roxabi_contracts.memory.models import (
    AssembleRequest,
    CaptureRequest,
    SearchRequest,
)


def _envelope_fields(trace_id: str | None = None) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "trace_id": trace_id or uuid4().hex,
        "issued_at": datetime.now(timezone.utc),
        "job_id": new_job_id(),
    }


def build_capture_request(  # noqa: PLR0913 — mirrors CaptureRequest fields
    *,
    title: str,
    body: str,
    category: str = "references",
    entry_type: str = "bookmark",
    url: str = "",
    tags: list[str] | None = None,
    namespace: str = "vault",
    metadata: dict | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> CaptureRequest:
    return CaptureRequest(
        **_envelope_fields(trace_id),
        request_id=request_id or uuid4().hex[:16],
        title=title,
        body=body,
        category=category,
        entry_type=entry_type,
        url=url,
        tags=tags or [],
        namespace=namespace,
        metadata=metadata or {},
    )


def build_search_request(  # noqa: PLR0913 — mirrors SearchRequest fields
    *,
    query: str,
    namespace: str | None = None,
    category: str | None = None,
    limit: int = 20,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> SearchRequest:
    return SearchRequest(
        **_envelope_fields(trace_id),
        request_id=request_id or uuid4().hex[:16],
        query=query,
        namespace=namespace,
        category=category,
        limit=limit,
    )


def build_assemble_request(  # noqa: PLR0913 — mirrors AssembleRequest fields
    *,
    goal: str | None = None,
    budget_tokens: int = 4000,
    namespace: str | None = None,
    user_id: str | None = None,
    fresh_tail_days: int = 7,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> AssembleRequest:
    return AssembleRequest(
        **_envelope_fields(trace_id),
        request_id=request_id or uuid4().hex[:16],
        goal=goal,
        budget_tokens=budget_tokens,
        namespace=namespace,
        user_id=user_id,
        fresh_tail_days=fresh_tail_days,
    )
