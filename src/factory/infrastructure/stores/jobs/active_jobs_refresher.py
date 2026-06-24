"""RegistryCoordinator — in-memory active-job index + KV liveness refresh (#1796).

Wraps an ``ActiveJobsPort`` with:

* an in-memory map (job_id → entry, worker_loc → job_id) as the source of
  truth for *which* jobs to refresh;
* a background loop that calls ``refresh_all()`` at *refresh_interval* seconds
  so that entries in the KV bucket never expire while the hub is alive
  (POOL-SOURCE liveness);
* ``on_heartbeat(worker_loc)`` for per-worker heartbeat tap-in that refreshes
  only the matching job's KV entry (HEARTBEAT-SOURCE liveness, Shape-D).
"""

from __future__ import annotations

import asyncio
import logging

import nats.errors

from factory.core.ports.active_jobs import ActiveJobEntry, ActiveJobsPort
from factory.infrastructure.stores.jobs.active_jobs_kv import ACTIVE_JOBS_REFRESH

log = logging.getLogger(__name__)


class RegistryCoordinator:
    """Coordinator that manages in-memory job maps and drives KV liveness.

    Parameters
    ----------
    port:
        Concrete ``ActiveJobsPort`` implementation (e.g. ``KvActiveJobsStore``).
    refresh_interval:
        How often (seconds) ``refresh_all()`` is called by the background loop.
        Defaults to ``ACTIVE_JOBS_REFRESH`` (30 s).
    """

    def __init__(
        self,
        port: ActiveJobsPort,
        refresh_interval: float = ACTIVE_JOBS_REFRESH,
    ) -> None:
        self._port = port
        self._refresh_interval = refresh_interval
        # job_id → ActiveJobEntry (in-memory source for refresh_all)
        self._jobs: dict[str, ActiveJobEntry] = {}
        # worker_loc → job_id (for heartbeat-driven refresh)
        self._by_loc: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None
        self._running: bool = False

    # ------------------------------------------------------------------
    # ActiveJobsPort-compatible surface (delegating + recording)
    # ------------------------------------------------------------------

    async def open(self, entry: ActiveJobEntry) -> None:
        """Delegate to port; record in in-memory maps on success.

        Raises
        ------
        RegistryConflictError
            Re-raised from port without recording (conflict = not opened).
        """
        # Propagate RegistryConflictError — do NOT record on conflict.
        await self._port.open(entry)
        self._jobs[entry.job_id] = entry
        if entry.worker_loc:
            self._by_loc[entry.worker_loc] = entry.job_id

    async def close(self, job_id: str) -> None:
        """Delegate to port; remove from in-memory maps."""
        await self._port.close(job_id)
        entry = self._jobs.pop(job_id, None)
        if entry is not None and entry.worker_loc:
            self._by_loc.pop(entry.worker_loc, None)

    # ------------------------------------------------------------------
    # Liveness
    # ------------------------------------------------------------------

    async def refresh_all(self) -> None:
        """Refresh every in-memory-tracked job in the KV store.

        POOL-SOURCE liveness: driven by the hub's background loop.  Iterates a
        snapshot of current job IDs so concurrent open/close during iteration
        does not mutate the set mid-loop.  One job's refresh failure is logged
        and skipped — it must not abort the sweep for the remaining jobs.
        """
        for jid in list(self._jobs):
            try:
                await self._port.refresh(jid)
            except (nats.errors.Error, OSError, RuntimeError):
                log.exception("active-jobs: refresh failed for job %r", jid)

    async def on_heartbeat(self, worker_loc: str) -> None:
        """Refresh the job mapped to *worker_loc*, if any.

        HEARTBEAT-SOURCE liveness (Shape-D): called by the heartbeat tap-in on
        the clipool ``WorkerPoolClient``.  A heartbeat for an unknown
        ``worker_loc`` is silently ignored — the coordinator only tracks jobs
        that were opened via ``open()``.
        """
        jid = self._by_loc.get(worker_loc)
        if jid is not None:
            await self._port.refresh(jid)

    # ------------------------------------------------------------------
    # Background loop lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background refresh task."""
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="active-jobs-refresher")

    async def stop(self) -> None:
        """Cancel and await the background task; suppress CancelledError."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            await self.refresh_all()
            await asyncio.sleep(self._refresh_interval)


__all__ = ["RegistryCoordinator"]
