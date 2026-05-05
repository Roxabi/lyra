"""Jobs-domain NATS subject strings and helpers."""

from dataclasses import dataclass

from roxabi_contracts._nats_utils import validate_job_token

__all__ = [
    "SUBJECTS",
    "jobs_submit",
    "jobs_result",
    "jobs_progress",
    "validate_job_token",
]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace for jobs-domain subjects.

    Empty at v0.4.0; callers use helpers.
    """

    pass


SUBJECTS = _Subjects()


def jobs_submit(job_name: str) -> str:
    """Submit subject: lyra.jobs.<job_name>."""
    validate_job_token(job_name)
    return f"lyra.jobs.{job_name}"


def jobs_result(job_id: str) -> str:
    """Result subject: lyra.results.<job_id>."""
    validate_job_token(job_id)
    return f"lyra.results.{job_id}"


def jobs_progress(job_id: str) -> str:
    """Progress subject: lyra.progress.<job_id>."""
    validate_job_token(job_id)
    return f"lyra.progress.{job_id}"
