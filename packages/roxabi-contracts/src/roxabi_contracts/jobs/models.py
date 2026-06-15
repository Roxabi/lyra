"""Jobs-domain NATS contract models.

Pure Pydantic. No NATS imports. No transport logic. Every model subclasses
ContractEnvelope, which provides (contract_version, trace_id, issued_at)
plus ConfigDict(extra="ignore") for forward-compat.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from roxabi_contracts._nats_utils import validate_job_token, validate_nats_subject
from roxabi_contracts.envelope import WorkEnvelope
from roxabi_contracts.errors import WorkerError

__all__ = ["JobEnvelope", "JobResult", "JobProgress"]


class JobEnvelope(WorkEnvelope):
    """Job submission envelope. Canonical subject: factory.jobs.<job_name>.

    ``job_id`` and ``parent_job_id`` are inherited from ``WorkEnvelope`` (ADR-084).

    ``payload`` is an untyped dict forwarded verbatim to the worker.  For omp
    jobs the V2 convention adds two *optional* routing hints (both absent on
    old senders — tolerated, treated as None):

        pool_id            – str | absent  – clipool pool to resume a session
        provider_session_id – str | absent  – opaque provider session handle
    """

    job_name: str
    payload: dict[str, Any]
    reply_to: str
    composite_depth: Annotated[int, Field(ge=0, le=3)] = 0

    @field_validator("job_name")
    @classmethod
    def _validate_job_tokens(cls, v: str) -> str:
        validate_job_token(v)
        return v

    @field_validator("reply_to")
    @classmethod
    def _validate_reply_to(cls, v: str) -> str:
        # Trust boundary: all JobEnvelope publishers are internal trusted services
        # on a private NATS cluster
        # (ADR-062 (absorbed into ADR-045)/064). No prefix restriction is applied
        # here; enforcement is at the ACL layer. If the cluster topology ever allows
        # untrusted publishers, restrict reply_to to _INBOX.* / _R_.* prefixes.
        validate_nats_subject(v)
        return v


class JobResult(WorkEnvelope):
    """Job reply envelope. Sent to reply_to subject on completion.

    ``job_id`` inherited from ``WorkEnvelope`` (ADR-084).

    ``data`` is an untyped dict returned by the worker.  For omp jobs the V2
    convention adds one *optional* field (absent on old workers — tolerated):

        session_file – str | absent  – path to the persisted omp session file
    """

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


class JobProgress(WorkEnvelope):
    """Job progress event. Published to factory.job.<job_id>.progress (best-effort).

    ``job_id`` inherited from ``WorkEnvelope`` (ADR-084).
    """

    step: Annotated[str, StringConstraints(min_length=1)]
    pct: Annotated[float | None, Field(ge=0.0, le=100.0)] = None
    detail: dict[str, Any] | None = None
    # Additive omp-worker streaming fields (default=None — wire-compatible, ADR-084)
    event_type: str | None = None
    partial_text: str | None = None
    tool_name: str | None = None
    tool_id: str | None = None
    tool_input: dict[str, Any] | None = None
