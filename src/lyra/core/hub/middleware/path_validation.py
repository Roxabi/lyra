"""Session resume path validation for SubmitToPoolMiddleware.

Three resume paths (priority order):
1. reply-to-resume via MessageIndex (#341)
2. thread-session-resume
3. last-active-session from TurnStore
"""

from __future__ import annotations

import logging

from ...messaging.message import InboundMessage
from ...pool import Pool
from ..pipeline.pipeline_types import ResumeStatus
from .middleware import PipelineContext

log = logging.getLogger(__name__)


async def resolve_context(
    msg: InboundMessage,
    pool: Pool,
    pool_id: str,
    ctx: PipelineContext,
) -> ResumeStatus:
    """Attempt session resume before pool.submit().

    Three paths (priority order): (1) reply-to-resume,
    (2) thread-session-resume, (3) last-active-session from TurnStore.

    Returns:
        RESUMED  -- a session was successfully resumed via any path.
        FRESH    -- Path 2 was attempted but rejected (session pruned /
                    invalid / expired); Claude will start fresh. The
                    caller should notify the user.
        SKIPPED  -- no resume was attempted (pool busy, group chat,
                    first use, no TurnStore, ...). Silent and expected.
    """
    result = await _resume_path1(msg, pool, pool_id, ctx)
    if result is not None:
        return result

    path2_result, path2_attempted = await _resume_path2(msg, pool, pool_id, ctx)
    if path2_result is not None:
        return path2_result

    result = await _resume_path3(msg, pool, pool_id, ctx)
    if result is not None:
        return result

    return ResumeStatus.FRESH if path2_attempted else ResumeStatus.SKIPPED


async def _resume_path1(
    msg: InboundMessage,
    pool: Pool,
    pool_id: str,
    ctx: PipelineContext,
) -> ResumeStatus | None:
    """Path 1: reply-to-resume via MessageIndex (#341). None = not applicable."""
    hub = ctx.hub
    if msg.reply_to_id is None:
        log.debug(
            "reply-to-resume[path1]: skip -- reply_to_id=None (msg_id=%s modality=%s)",
            msg.id,
            msg.modality,
        )
        return None
    if hub._message_index is None:
        log.debug("reply-to-resume[path1]: skip -- no MessageIndex configured")
        return None
    session_id = await hub._message_index.resolve(pool_id, str(msg.reply_to_id))
    log.debug(
        "reply-to-resume[path1]: reply_to_id=%r -> session_id=%r (pool=%s)",
        msg.reply_to_id,
        session_id,
        pool_id,
    )
    if session_id is None:
        return None
    if not pool.is_idle:
        pool._pending_session_id = session_id  # was: log + skip
        log.info(
            "reply-to-resume[path1]: pool %r busy -- queued session %r",
            pool_id,
            session_id,
        )
        return ResumeStatus.SKIPPED
    log.info(
        "reply-to-resume[path1]: resuming session %r for pool %r",
        session_id,
        pool_id,
    )
    accepted = await pool.resume_session(session_id)
    log.debug(
        "reply-to-resume[path1]: resume_session accepted=%s (session=%r pool=%s)",
        accepted,
        session_id,
        pool_id,
    )
    return ResumeStatus.RESUMED


async def _resume_path2(
    msg: InboundMessage,
    pool: Pool,
    pool_id: str,
    ctx: PipelineContext,
) -> tuple[ResumeStatus | None, bool]:
    """Path 2: thread-session-resume. Returns (status|None, attempted)."""
    hub = ctx.hub
    thread_session_id: str | None = getattr(msg.platform_meta, "thread_session_id", None)
    if thread_session_id is None:
        return None, False

    # Scope-validate: session must belong to this pool (#525).
    if hub._turn_store is None:
        # No TurnStore -> cannot validate scope -> safe default: skip.
        log.debug(
            "thread-session-resume: no TurnStore -- skipping %r",
            thread_session_id,
        )
        return ResumeStatus.SKIPPED, False

    session_pool = await hub._turn_store.get_session_pool_id(thread_session_id)
    if session_pool is None or session_pool != pool_id:
        log.warning(
            "thread-session-resume: scope mismatch for %r -- "
            "expected pool %r, got %r -- skipping",
            thread_session_id,
            pool_id,
            session_pool,
        )
        return ResumeStatus.SKIPPED, False

    if not pool.is_idle:
        log.info(
            "thread-session-resume: pool %r busy -- skipping %r",
            pool_id,
            thread_session_id,
        )
        return ResumeStatus.SKIPPED, False

    if thread_session_id == pool.session_id:
        log.debug(
            "thread-session-resume: pool %r already on session %r -- skipping",
            pool_id,
            thread_session_id,
        )
        return ResumeStatus.SKIPPED, False

    log.info(
        "thread-session-resume: resuming %r for pool %r",
        thread_session_id,
        pool_id,
    )
    accepted = await pool.resume_session(thread_session_id)
    if accepted:
        return ResumeStatus.RESUMED, True
    log.info(
        "thread-session-resume: session %r not accepted -- falling through to Path 3",
        thread_session_id,
    )
    return None, True


async def _resume_path3(
    msg: InboundMessage,
    pool: Pool,
    pool_id: str,
    ctx: PipelineContext,
) -> ResumeStatus | None:
    """Path 3: last-active-session from TurnStore. None = not applicable."""
    hub = ctx.hub
    if not pool.is_idle or hub._turn_store is None:
        return None

    last_sid = await hub._turn_store.get_last_session(pool_id)
    if last_sid is None:
        log.debug(
            "last-session-resume: no prior session for pool %r",
            pool_id,
        )
        return None

    if last_sid == pool.session_id:
        _agent = hub.agent_registry.get(pool.agent_name)
        _alive = _agent.is_backend_alive(pool.pool_id) if _agent is not None else True
        if _alive:
            log.debug(
                "last-session-resume: pool %r already on session %r",
                pool_id,
                last_sid,
            )
            return ResumeStatus.SKIPPED
        log.warning(
            "last-session-resume: pool %r session %r matches"
            " but backend is dead -- skipping guard",
            pool_id,
            last_sid,
        )
        return None

    log.info(
        "last-session-resume: resuming %r for pool %r",
        last_sid,
        pool_id,
    )
    await pool.resume_session(last_sid)
    return ResumeStatus.RESUMED
