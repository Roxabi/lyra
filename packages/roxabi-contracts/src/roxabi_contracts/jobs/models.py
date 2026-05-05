"""Jobs-domain NATS contract models.

Pure Pydantic. No NATS imports. No transport logic. Every model subclasses
ContractEnvelope, which provides (contract_version, trace_id, issued_at)
plus ConfigDict(extra="ignore") for forward-compat.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from roxabi_contracts._nats_utils import validate_job_token
from roxabi_contracts.envelope import ContractEnvelope
from roxabi_contracts.errors import WorkerError

__all__ = ["JobEnvelope", "JobResult", "JobProgress"]


class JobEnvelope(ContractEnvelope):
    """Job submission envelope. Canonical subject: lyra.jobs.<job_name>."""

    job_id: str
    job_name: str
    payload: dict[str, Any]
    reply_to: str
    parent_job_id: str | None = None
    composite_depth: Annotated[int, Field(ge=0, le=3)] = 0

    @field_validator("job_id", "job_name", "reply_to")
    @classmethod
    def _validate_nats_tokens(cls, v: str) -> str:
        validate_job_token(v)
        return v

    @field_validator("parent_job_id")
    @classmethod
    def _validate_parent_job_id(cls, v: str | None) -> str | None:
        if v is not None:
            validate_job_token(v)
        return v


class JobResult(ContractEnvelope):
    """Job reply envelope. Sent to reply_to subject on completion."""

    job_id: str
    status: Literal["success", "error"]
    data: dict[str, Any] | None = None
    error: WorkerError | None = None

    @field_validator("job_id")
    @classmethod
    def _validate_job_id(cls, v: str) -> str:
        validate_job_token(v)
        return v

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

    job_id: str
    step: Annotated[str, StringConstraints(min_length=1)]
    pct: Annotated[float, Field(ge=0.0, le=100.0)] | None = None
    detail: dict[str, Any] | None = None

    @field_validator("job_id")
    @classmethod
    def _validate_job_id(cls, v: str) -> str:
        validate_job_token(v)
        return v
