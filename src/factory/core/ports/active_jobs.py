"""Driven port — ActiveJobsPort: per-pool active-job registry (#1796).

Pure Protocol + value objects only.  Zero infrastructure imports.
Implementations live in ``factory.infrastructure.stores.jobs.active_jobs_kv``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class RegistryConflictError(Exception):
    """Raised when ``open()`` finds a non-expired job already mapped to *pool_id*.

    Attributes
    ----------
    pool_id:
        The pool that already has an active job.
    existing_job_id:
        The job_id that blocked the new registration.
    """

    def __init__(self, pool_id: str, existing_job_id: str) -> None:
        super().__init__(f"pool {pool_id!r} already has active job {existing_job_id!r}")
        self.pool_id = pool_id
        self.existing_job_id = existing_job_id


@dataclass(frozen=True)
class ActiveJobEntry:
    """Immutable snapshot of a single active job.

    Fields
    ------
    job_id:
        Opaque unique identifier for this job (e.g. a UUID).
    pool_id:
        The pool this job belongs to (``RoutingKey.to_pool_id()``).
    status:
        ``"open"`` while running; ``"closing"`` during graceful teardown.
    started_at:
        UTC timestamp when the job was opened.
    steer_subject:
        NATS subject the worker listens on for steering signals.
    concurrency_mode:
        ``"steer"`` — singleton guarded via KV CAS index.
        ``"queue"`` — singleton guarded via KV CAS index.
        ``"parallel"`` — no singleton index; multiple jobs allowed per pool.
    worker_loc:
        Optional worker location hint (e.g. host:port or container id).
    """

    job_id: str
    pool_id: str
    status: str
    started_at: datetime.datetime
    steer_subject: str
    concurrency_mode: str
    worker_loc: str | None = None


@runtime_checkable
class ActiveJobsPort(Protocol):
    """Secondary (driven) port for the active-jobs KV registry.

    All methods are async.  Callers must ``open()`` a bucket handle before use
    (provider-managed lifecycle — typically done via ``ensure_active_jobs_kv``
    at hub startup).
    """

    async def open(self, entry: ActiveJobEntry) -> None:
        """Register *entry* as the active job for its pool.

        Raises
        ------
        RegistryConflictError
            If *pool_id* already maps to a different, non-expired job
            (steer/queue modes only).
        """
        ...

    async def refresh(self, job_id: str) -> None:
        """Extend the TTL of *job_id* to prevent expiry.

        No-op if *job_id* is not found (job may have already closed).
        """
        ...

    async def close(self, job_id: str) -> None:
        """Remove *job_id* from the registry.

        Cleans up both the per-job key and the pool→job index entry when
        this job owns the index.  No-op if *job_id* is not found.
        """
        ...

    async def get_by_pool(self, pool_id: str) -> ActiveJobEntry | None:
        """Return the active job for *pool_id*, or ``None`` if absent/expired."""
        ...


@runtime_checkable
class ActiveJobsRecorder(Protocol):
    """Narrow open/close surface the Pool needs to register a run (#1772).

    A strict subset of :class:`ActiveJobsPort` — satisfied structurally by
    ``RegistryCoordinator`` (which delegates to the port and maintains the
    in-memory snapshot the dashboard reads).  Kept separate so ``core.pool``
    depends only on the two methods it calls, not the full store surface.
    """

    async def open(self, entry: ActiveJobEntry) -> None:
        """Register *entry* as the active job for its pool."""
        ...

    async def close(self, job_id: str) -> None:
        """Remove *job_id* from the registry (no-op if absent)."""
        ...
