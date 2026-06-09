"""Session resume path validation for SubmitToPoolMiddleware.

Two resume paths (priority order):
1. reply-to-resume via MessageIndex (#341)
2. last-active-session from TurnStore (#1777: path-2 removed)
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

    Two paths (priority order): (1) reply-to-resume via MessageIndex,
    (2) last-active-session from TurnStore. Path-2 (thread-session-resume)
    was removed in #1777.

    Returns:
        RESUMED  -- a session was successfully resumed via any path.
        SKIPPED  -- no resume was attempted (pool busy, first use,
                    no TurnStore, ...). Silent and expected.
    """
    result = await _resume_path1(msg, pool, pool_id, ctx)
    if result is not None:
        return result

    result = await _resume_path3(msg, pool, pool_id, ctx)
    if result is not None:
        return result

    return ResumeStatus.SKIPPED


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
