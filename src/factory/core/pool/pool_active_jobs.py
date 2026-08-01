"""Pool → active-jobs registry side-channel (#1772, #2147).

Open/close helpers live here so ``pool_processor`` and ``pool_processor_exec``
can share them without a circular import.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from factory.core.ports.active_jobs import (
    ActiveJobEntry,
    RegistryConflictError,
    concurrency_mode_for_backend,
)
from roxabi_contracts import new_job_id
from roxabi_contracts.jobs.subjects import jobs_steer

if TYPE_CHECKING:
    from factory.core.pool.pool import Pool
    from factory.core.ports.active_jobs import ActiveJobsRecorder

log = logging.getLogger(__name__)


def _active_jobs_recorder(pool: Pool) -> ActiveJobsRecorder | None:
    """The hub's active-jobs recorder (``None`` until wired), via ``PoolContext``."""
    return pool._ctx.active_jobs_recorder()


def _pool_backend(pool: Pool) -> str | None:
    """Best-effort agent backend for concurrency_mode (#2130)."""
    try:
        agent = pool._ctx.get_agent(pool.agent_name)
    except Exception:  # noqa: BLE001 — side-channel
        return None
    if agent is None:
        return None
    cfg = getattr(agent, "llm_config", None) or getattr(agent, "config", None)
    return getattr(cfg, "backend", None) if cfg is not None else None


async def open_active_job(pool: Pool, *, job_id: str | None = None) -> str | None:
    """Register this turn in the active-jobs registry; return its job_id.

    *job_id* must be the wire envelope id (TraceContext root / new_job_id) so
    steer/cancel and ResultCloseListener match the worker (#2147).
    """
    try:
        recorder = _active_jobs_recorder(pool)
        if recorder is None:
            return None
        resolved = job_id or new_job_id()
        mode = concurrency_mode_for_backend(_pool_backend(pool))
        entry = ActiveJobEntry(
            job_id=resolved,
            pool_id=pool.pool_id,
            status="open",
            started_at=datetime.now(UTC),
            steer_subject=jobs_steer(resolved),
            concurrency_mode=mode,
        )
        await recorder.open(entry)
    except RegistryConflictError:
        log.info(
            "[pool:%s] active-jobs: pool already has an open job — "
            "run not registered (TTL will heal)",
            pool.pool_id,
        )
        return None
    except Exception:  # noqa: BLE001 — side-channel: never break the turn
        log.warning(
            "[pool:%s] active-jobs: open failed — run not registered",
            pool.pool_id,
            exc_info=True,
        )
        return None
    return resolved


async def close_active_job(pool: Pool, job_id: str | None) -> None:
    """Remove this turn from the registry; best-effort (TTL heals a miss)."""
    if job_id is None:
        return
    try:
        recorder = _active_jobs_recorder(pool)
        if recorder is None:
            return
        await recorder.close(job_id)
    except Exception:  # noqa: BLE001 — side-channel: never break the turn
        log.warning(
            "[pool:%s] active-jobs: close failed for %r",
            pool.pool_id,
            job_id,
            exc_info=True,
        )


# Back-compat aliases used by tests that import private names.
_open_active_job = open_active_job
_close_active_job = close_active_job

__all__ = [
    "close_active_job",
    "open_active_job",
    "_close_active_job",
    "_open_active_job",
]
