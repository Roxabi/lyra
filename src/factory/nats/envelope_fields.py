"""SSoT for WorkEnvelope correlation fields on hub NATS codec encode paths (#2069).

Hub work-path codecs must call ``mint_work_envelope_fields()`` instead of
minting a fresh ``trace_id`` per hop. See otel-correlation-ids-spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from factory.core.trace import TraceContext
from roxabi_contracts import new_job_id
from roxabi_contracts.envelope import CONTRACT_VERSION


@dataclass(frozen=True)
class WorkEnvelopeFields:
    """Correlation fields stamped on outbound work envelopes."""

    contract_version: str
    trace_id: str
    issued_at: datetime
    job_id: str
    parent_job_id: str | None
    pool_id: str | None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "contract_version": self.contract_version,
            "trace_id": self.trace_id,
            "issued_at": self.issued_at,
            "job_id": self.job_id,
        }
        if self.parent_job_id is not None:
            out["parent_job_id"] = self.parent_job_id
        if self.pool_id is not None:
            out["pool_id"] = self.pool_id
        return out


def mint_work_envelope_fields(
    *,
    trace_id: str | None = None,
    job_id: str | None = None,
    parent_job_id: str | None = None,
    pool_id: str | None = None,
) -> WorkEnvelopeFields:
    """Mint envelope correlation fields from explicit args or TraceContext.

    Resolution order:
    - ``trace_id``: explicit → ``TraceContext.get_trace_id()`` → raises if unset
    - ``pool_id``: explicit → ``TraceContext.get_pool_id()``
    - ``job_id``: explicit → ``new_job_id()`` (ingress should pass ``root_job_id``)
    """
    resolved_trace = trace_id or TraceContext.get_trace_id()
    if not resolved_trace:
        msg = (
            "mint_work_envelope_fields: trace_id required "
            "(set TraceContext or pass explicit)"
        )
        raise ValueError(msg)

    resolved_pool = pool_id if pool_id is not None else TraceContext.get_pool_id()
    resolved_job = job_id if job_id is not None else new_job_id()

    return WorkEnvelopeFields(
        contract_version=CONTRACT_VERSION,
        trace_id=resolved_trace,
        issued_at=datetime.now(tz=UTC),
        job_id=resolved_job,
        parent_job_id=parent_job_id,
        pool_id=resolved_pool,
    )