"""Jobs-domain NATS contract surface.

Public API: three envelope models + SUBJECTS namespace + subject helpers.
fixtures submodule is test-only — import explicitly as
``from roxabi_contracts.jobs.fixtures import ...``.
"""

from roxabi_contracts.jobs.models import JobEnvelope, JobProgress, JobResult
from roxabi_contracts.jobs.subjects import (
    SUBJECTS,
    jobs_progress,
    jobs_result,
    jobs_submit,
)

__all__ = [
    "JobEnvelope",
    "JobProgress",
    "JobResult",
    "SUBJECTS",
    "jobs_progress",
    "jobs_result",
    "jobs_submit",
]
