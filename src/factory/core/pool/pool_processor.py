"""Processing engine extracted from Pool (issue #300).

Owns cancel-in-flight loop, guarded execution, timeout handling, and
dispatch logic.  Pool retains session state and public API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import AgentBase
    from ..messaging.message import InboundMessage
    from ..ports.active_jobs import ActiveJobsRecorder
    from .pool import Pool

from roxabi_contracts.jobs.subjects import jobs_steer

from ..messaging.message import Response
from ..ports.active_jobs import ActiveJobEntry, RegistryConflictError
from .pool_observer import _TURN_PERSIST_ERRORS
from .pool_processor_exec import _safe_dispatch, guarded_process_one

log = logging.getLogger(__name__)


def _active_jobs_recorder(pool: Pool) -> ActiveJobsRecorder | None:
    """The hub's active-jobs recorder (``None`` until wired), via ``PoolContext``.

    Resolved in one place so open/close agree; callers wrap this in the
    best-effort try/except that guards the whole registry side-channel, so a
    context that raises here cannot break the turn either.
    """
    return pool._ctx.active_jobs_recorder()


async def _open_active_job(pool: Pool) -> str | None:
    """Register this run in the active-jobs registry; return its job_id.

    Best-effort side-channel: the registry is observability + routing support,
    never on the turn's critical path.  The ENTIRE body — recorder resolution,
    entry construction and ``open()`` — is guarded so no registry or context
    failure can ever propagate into message processing.
    """
    try:
        recorder = _active_jobs_recorder(pool)
        if recorder is None:
            return None
        job_id = uuid.uuid4().hex
        entry = ActiveJobEntry(
            job_id=job_id,
            pool_id=pool.pool_id,
            status="open",
            started_at=datetime.now(UTC),
            steer_subject=jobs_steer(job_id),
            concurrency_mode="steer",
        )
        await recorder.open(entry)
    except RegistryConflictError:
        # Benign + self-healing: a prior run's entry for this pool lingers
        # until its TTL expires; this run is simply not registered meanwhile.
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
    return job_id


async def _close_active_job(pool: Pool, job_id: str | None) -> None:
    """Remove this run from the registry; best-effort (TTL heals a miss).

    Whole body guarded — a registry/context failure on the teardown path
    (incl. during ``asyncio.CancelledError`` unwind) must never propagate.
    """
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


class PoolProcessor:
    """Async processing engine for a Pool.

    Encapsulates the debounce → cancel-in-flight → guarded-execute → dispatch
    pipeline.  Holds no session state — reads everything from the owning Pool.
    Internal to Pool — do not instantiate directly.
    """

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def process_loop(self) -> None:  # noqa: C901 — DEBT:wiring-bootstrap-deps — debounce + cancel-in-flight adds inherent branches
        """Consume inbox with debounce aggregation and cancel-in-flight."""
        pool = self._pool
        _last_msg: InboundMessage | None = None
        _job_id: str | None = None
        try:
            _job_id = await _open_active_job(pool)
            while True:
                if pool._inbox.empty():
                    break
                buffer = await pool._debouncer.collect(pool._inbox)

                # Fire any pending reply-to session resume before processing
                if pool._pending_session_id is not None:
                    _pending = pool._pending_session_id
                    pool._pending_session_id = None
                    try:
                        _resume_accepted = await pool.resume_session(_pending)
                    except _TURN_PERSIST_ERRORS:
                        log.exception(
                            "[pool:%s] pending session resume failed for %r"
                            " — continuing",
                            pool.pool_id,
                            _pending,
                        )
                        _resume_accepted = False
                    if not _resume_accepted:
                        log.info(
                            "[pool:%s] pending session resume rejected for %r",
                            pool.pool_id,
                            _pending,
                        )

                agent = pool._ctx.get_agent(pool.agent_name)
                if agent is None:
                    log.error(
                        "no agent %r for pool %s — message(s) dropped",
                        pool.agent_name,
                        pool.pool_id,
                    )
                    # Drain any remaining queued messages before exiting
                    while not pool._inbox.empty():
                        try:
                            pool._inbox.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    break

                # Phase 2: process with cancel-in-flight
                msg = pool._debouncer.merge(buffer)
                _last_msg = msg
                _last_msg = await self._process_with_cancel(msg, buffer, agent)
        except asyncio.CancelledError:
            # /stop cancellation — send a reply if we have a message context.
            if _last_msg is not None:
                _reply = pool._msg("cancelled", "Request cancelled.")
                await asyncio.shield(
                    _safe_dispatch(_last_msg, Response(content=_reply), pool)
                )
            raise
        finally:
            pool._current_task = None
            await _close_active_job(pool, _job_id)

    async def _process_with_cancel(
        self,
        msg: InboundMessage,
        buffer: list[InboundMessage],
        agent: AgentBase,
    ) -> InboundMessage:
        """Run agent.process(), with optional cancel-in-flight on new messages.

        Default mode (cancel_on_new_message=False): awaits the agent to completion;
        any messages that arrive during processing queue naturally in pool._inbox and
        are picked up by the next process_loop() iteration.

        Cancel-in-flight mode (cancel_on_new_message=True): races the agent against
        the inbox; a new message aborts the ongoing turn, merges the context, and
        re-dispatches from scratch.
        """
        pool = self._pool

        while True:
            agent_task = asyncio.create_task(
                guarded_process_one(msg, agent, pool),
                name=f"agent:{pool.pool_id}",
            )

            if not pool.cancel_on_new_message:
                # "Let it finish" mode — new messages accumulate in inbox and are
                # consumed naturally by the next process_loop() iteration.
                await agent_task
                if agent_task.cancelled():
                    raise asyncio.CancelledError
                return msg

            # ----------------------------------------------------------------
            # Cancel-in-flight mode: race agent against next inbox message.
            # ----------------------------------------------------------------
            inbox_waiter = asyncio.create_task(
                pool._inbox.get(), name=f"inbox:{pool.pool_id}"
            )

            done, _ = await asyncio.wait(
                {agent_task, inbox_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if agent_task in done:
                # Agent finished — errors handled inside _guarded_process_one.
                inbox_waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await inbox_waiter
                # If inbox_waiter also completed (race), put the message back.
                if inbox_waiter in done and not inbox_waiter.cancelled():
                    try:
                        pool._inbox.put_nowait(inbox_waiter.result())
                    except asyncio.QueueFull:
                        log.warning(
                            "pool %s: message lost in inbox race (queue full)",
                            pool.pool_id,
                        )
                # Propagate CancelledError from /stop.
                if agent_task.cancelled():
                    raise asyncio.CancelledError
                return msg

            # New message arrived while agent was processing → cancel-in-flight.
            new_msg = inbox_waiter.result()
            # Mark any in-flight streaming as superseded BEFORE yielding to the
            # event loop.  asyncio is single-threaded: the flag is guaranteed to
            # be visible to the dispatcher worker the next time it runs (i.e.
            # after the `await agent_task` below).  This prevents the dispatcher
            # from sending an orphaned "…" placeholder for the cancelled turn.
            _inflight = pool._inflight_stream_outbound
            if _inflight is not None:
                _inflight.metadata["_superseded"] = True
                pool._inflight_stream_outbound = None
            agent_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await agent_task

            log.debug(
                "cancel-in-flight in pool %s: new message while LLM processing",
                pool.pool_id,
            )

            # Debounce the new message (drain any rapid follow-ups).
            buffer.append(new_msg)
            buffer.extend(await pool._debouncer.drain_followups(pool._inbox))

            msg = pool._debouncer.merge(buffer)
