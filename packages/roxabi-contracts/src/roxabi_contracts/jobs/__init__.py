"""Jobs-domain NATS contract surface.

Public API: three envelope models + SUBJECTS namespace + subject helpers.
fixtures submodule is test-only — import explicitly as
``from roxabi_contracts.jobs.fixtures import ...``.
"""

from roxabi_contracts.jobs.models import JobEnvelope, JobProgress, JobResult
from roxabi_contracts.jobs.subjects import (
    RUNTIME_JOB_CLAUDE,
    RUNTIME_JOB_OMP,
    SUBJECTS,
    jobs_closed,
    jobs_opened,
    jobs_progress,
    jobs_result,
    jobs_runtime_claude,
    jobs_runtime_omp,
    jobs_steer,
    jobs_submit,
)

__all__ = [
    "JobEnvelope",
    "JobProgress",
    "JobResult",
    "RUNTIME_JOB_CLAUDE",
    "RUNTIME_JOB_OMP",
    "SUBJECTS",
    "jobs_closed",
    "jobs_opened",
    "jobs_progress",
    "jobs_result",
    "jobs_runtime_claude",
    "jobs_runtime_omp",
    "jobs_steer",
    "jobs_submit",
]
