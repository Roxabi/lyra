"""Jobs-domain NATS contract models.

Pure Pydantic. No NATS imports. No transport logic. Every model subclasses
ContractEnvelope, which provides (contract_version, trace_id, issued_at)
plus ConfigDict(extra="ignore") for forward-compat.

Validators for composite_depth and JobResult status/error mutex are in
models.py but added in the GREEN phase (T7).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import StringConstraints

from roxabi_contracts.envelope import ContractEnvelope
from roxabi_contracts.errors import WorkerError

__all__ = ["JobEnvelope", "JobResult", "JobProgress"]


class JobEnvelope(ContractEnvelope):
    """Job submission envelope. Canonical subject: lyra.jobs.<job_name>."""
    job_id: Annotated[str, StringConstraints(min_length=1)]
    job_name: Annotated[str, StringConstraints(min_length=1)]
    payload: dict[str, Any]
    reply_to: Annotated[str, StringConstraints(min_length=1)]
    parent_job_id: str | None = None
    composite_depth: int = 0


class JobResult(ContractEnvelope):
    """Job reply envelope. Sent to reply_to subject on completion."""
    job_id: Annotated[str, StringConstraints(min_length=1)]
    status: Literal["success", "error"]
    data: dict[str, Any] | None = None
    error: WorkerError | None = None


class JobProgress(ContractEnvelope):
    """Job progress event. Published to lyra.progress.<job_id> (best-effort)."""
    job_id: Annotated[str, StringConstraints(min_length=1)]
    step: Annotated[str, StringConstraints(min_length=1)]
    pct: float | None = None
    detail: dict[str, Any] | None = None
