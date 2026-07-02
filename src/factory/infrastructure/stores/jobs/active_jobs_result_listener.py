"""ResultCloseListener — close registry entries on terminal JobResult (#1795).

Subscribes ``JOB_RESULT_WILDCARD`` (``factory.job.*.result``) on the hub's
core NATS connection and closes the matching active-jobs registry entry when
a job's terminal result arrives.  The subject itself is the close signal —
the payload is never deserialized, so a malformed ``JobResult`` still closes
the entry.

This is the authoritative close of ``docs/architecture/job-model.md``, but
it is mostly dormant today: every pool run's registry entry is keyed by a
locally-minted uuid4 (``pool_processor._open_active_job``) that never equals
the wire job_id, so ``close(wire_id)`` no-ops until #2142/#2147 key entries
by the envelope job_id.  The pool-loop ``finally`` close stays load-bearing
until then.

Trust boundary: the publish ACL on ``factory.job.*.result`` (clipool/omp
workers) is the only authorization — any grant holder can name any job_id
and close its entry.  Per-job ownership correlation is deferred to the
concurrency-router chain (#1797/#1799).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import nats.errors

from roxabi_contracts.jobs.subjects import JOB_RESULT_WILDCARD

if TYPE_CHECKING:
    from nats.aio.msg import Msg

    from factory.infrastructure.stores.jobs.active_jobs_refresher import (
        RegistryCoordinator,
    )

log = logging.getLogger(__name__)


class ResultCloseListener:
    """Subscribe to every job's terminal result and close its registry entry.

    Best-effort by design: a close failure is logged and dropped — the
    coordinator untracks the entry even on failure, so the leftover KV key
    expires via TTL; closing an untracked job is a no-op.
    """

    def __init__(self, nc: Any, coordinator: "RegistryCoordinator") -> None:
        self._nc = nc
        self._coordinator = coordinator
        self._sub: Any = None

    async def start(self) -> None:
        """Subscribe to the result wildcard."""
        self._sub = await self._nc.subscribe(JOB_RESULT_WILDCARD, cb=self._handle)
        log.info("ResultCloseListener: subscribed to %s", JOB_RESULT_WILDCARD)

    async def stop(self) -> None:
        """Unsubscribe from the result wildcard.

        Never raises — a closing/drained connection tears the subscription
        down server-side anyway, and a raise here would skip the remaining
        shutdown steps in the caller.
        """
        if self._sub is None:
            return
        try:
            await self._sub.unsubscribe()
        except (nats.errors.Error, OSError, RuntimeError):
            log.warning(
                "ResultCloseListener: unsubscribe failed during stop", exc_info=True
            )
        else:
            log.info("ResultCloseListener: unsubscribed.")
        finally:
            self._sub = None

    async def _handle(self, msg: "Msg") -> None:
        """Close the registry entry for the job named in the subject."""
        job_id = _extract_job_id(msg.subject)
        if job_id is None:
            log.warning(
                "ResultCloseListener: unexpected subject %r — skipping", msg.subject
            )
            return
        try:
            await self._coordinator.close(job_id)
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: nats-callback — a corrupt KV entry raises json/KeyError; must not escape the sub callback
            log.exception(
                "ResultCloseListener: close failed for job %r"
                " — leftover KV entry expires via TTL",
                job_id,
            )


def _extract_job_id(subject: str) -> str | None:
    """Extract the job_id token from a ``factory.job.<job_id>.result`` subject.

    Returns ``None`` for any subject that does not match the expected shape.
    """
    parts = subject.split(".")
    if (
        len(parts) == 4
        and parts[0] == "factory"
        and parts[1] == "job"
        and parts[2]
        and parts[3] == "result"
    ):
        return parts[2]
    return None


__all__ = ["ResultCloseListener"]
