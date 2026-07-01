"""Jobs-domain NATS subject strings and helpers."""

from dataclasses import dataclass
from typing import Literal

from roxabi_contracts._nats_utils import validate_job_token

RUNTIME_JOB_CLAUDE = "claude"
RUNTIME_JOB_OMP = "omp"

# Reserved steer payload — dashboard cancel (#1773) publishes this on factory.job.<id>.steer.
JOB_CANCEL_STEER_TOKEN = "__factory_cancel__"

__all__ = [
    "SUBJECTS",
    "RUNTIME_JOB_CLAUDE",
    "RUNTIME_JOB_OMP",
    "jobs_submit",
    "jobs_result",
    "jobs_progress",
    "jobs_steer",
    "jobs_opened",
    "jobs_closed",
    "jobs_runtime_claude",
    "jobs_runtime_omp",
    "JOB_CANCEL_STEER_TOKEN",
]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace for jobs-domain subject prefixes.

    Holds the static prefix halves of each subject family. The dynamic
    suffix (job_name or job_id) is appended by the helper functions below.
    Diverges from voice/image/llm where SUBJECTS holds complete Literal
    subjects — jobs subjects are always parameterised.
    """

    submit_prefix: Literal["factory.jobs"] = "factory.jobs"
    job_prefix: Literal["factory.job"] = "factory.job"


SUBJECTS = _Subjects()


def jobs_submit(job_name: str) -> str:
    """Submit subject: factory.jobs.<job_name>."""
    validate_job_token(job_name)
    return f"factory.jobs.{job_name}"


def jobs_runtime_claude() -> str:
    """Claude-cli harness dispatch lane (core-NATS queue group)."""
    return jobs_submit(RUNTIME_JOB_CLAUDE)


def jobs_runtime_omp() -> str:
    """OMP harness dispatch lane (core-NATS queue group)."""
    return jobs_submit(RUNTIME_JOB_OMP)


def jobs_result(job_id: str) -> str:
    """Result subject: factory.job.<job_id>.result."""
    validate_job_token(job_id)
    return f"factory.job.{job_id}.result"


def jobs_progress(job_id: str) -> str:
    """Progress subject: factory.job.<job_id>.progress."""
    validate_job_token(job_id)
    return f"factory.job.{job_id}.progress"


def jobs_steer(job_id: str) -> str:
    """Steer subject: factory.job.<job_id>.steer."""
    validate_job_token(job_id)
    return f"factory.job.{job_id}.steer"


def jobs_opened(job_id: str) -> str:
    """Lifecycle open event subject: factory.job.<job_id>.opened."""
    validate_job_token(job_id)
    return f"factory.job.{job_id}.opened"


def jobs_closed(job_id: str) -> str:
    """Lifecycle close event subject: factory.job.<job_id>.closed."""
    validate_job_token(job_id)
    return f"factory.job.{job_id}.closed"
