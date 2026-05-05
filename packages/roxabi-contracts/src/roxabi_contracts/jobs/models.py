"""Jobs-domain NATS contract models.

Pure Pydantic. No NATS imports. No transport logic. Every model subclasses
ContractEnvelope, which provides (contract_version, trace_id, issued_at)
plus ConfigDict(extra="ignore") for forward-compat.

Validators for composite_depth and JobResult status/error mutex are in
models.py but added in the GREEN phase (T7).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import StringConstraints, field_validator, model_validator

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

    @field_validator("composite_depth")
    @classmethod
    def _validate_depth(cls, v: int) -> int:
        if not (0 <= v <= 3):
            raise ValueError(f"composite_depth must be 0–3, got {v}")
        return v


class JobResult(ContractEnvelope):
    """Job reply envelope. Sent to reply_to subject on completion."""

    job_id: Annotated[str, StringConstraints(min_length=1)]
    status: Literal["success", "error"]
    data: dict[str, Any] | None = None
    error: WorkerError | None = None

    @model_validator(mode="after")
    def _enforce_status_invariant(self) -> Self:
        if self.status == "error" and self.error is None:
            raise ValueError("JobResult with status='error' must carry a WorkerError")
        if self.status == "success" and self.error is not None:
            raise ValueError(
                "JobResult with status='success' must not carry a WorkerError"
            )
        return self


class JobProgress(ContractEnvelope):
    """Job progress event. Published to lyra.progress.<job_id> (best-effort)."""

    job_id: Annotated[str, StringConstraints(min_length=1)]
    step: Annotated[str, StringConstraints(min_length=1)]
    pct: float | None = None
    detail: dict[str, Any] | None = None
