"""Terminal middleware stage: pool submission + session resume (#431).

``SubmitToPoolMiddleware`` is the final stage in the default pipeline.
It validates the adapter, checks the circuit breaker, attempts session
resume via three paths, and returns ``SUBMIT_TO_POOL``.
"""

from __future__ import annotations

import logging

from ..message import (
    InboundMessage,
    Platform,
)
from ..pool import Pool
from .message_pipeline import (
    _DROP,
    _SESSION_FALLTHROUGH_MSG,
    Action,
    PipelineResult,
    ResumeStatus,
)
from .middleware import Next, PipelineContext
from .pipeline_events import MessageDropped, PoolSubmitted

log = logging.getLogger(__name__)


class SubmitToPoolMiddleware:
    """Stage 6 (terminal): validate adapter, circuit breaker, submit."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        if ctx.pool is None:
            raise RuntimeError(
                "CreatePoolMiddleware must precede SubmitToPoolMiddleware"
            )
        if ctx.key is None:
            raise RuntimeError(
                "RateLimitMiddleware must precede SubmitToPoolMiddleware"
            )

        if (ctx.key.platform, msg.bot_id) not in ctx.hub.adapter_registry:
            log.error(
                "no adapter registered for (%s, %s) — response dropped",
                msg.platform,
                msg.bot_id,
            )
            ctx.trace(
                "outbound",
                "no_adapter",
                platform=msg.platform,
                bot_id=msg.bot_id,
                action=Action.DROP.value,
            )
            ctx.emit(
                MessageDropped(
                    msg_id=msg.id,
                    stage=type(self).__name__,
                    reason="no_adapter",
                )
            )
            return _DROP

        if await ctx.hub.circuit_breaker_drop(msg):
            ctx.trace("outbound", "circuit_open", action=Action.DROP.value)
            ctx.emit(
                MessageDropped(
                    msg_id=msg.id,
                    stage=type(self).__name__,
                    reason="circuit_open",
                )
            )
            return _DROP

        pool = ctx.pool
        # Register session persistence callback once.
        _update_fn = msg.platform_meta.get("_session_update_fn")
        if _update_fn is not None and pool._observer._session_update_fn is None:
            pool._observer.register_session_update_fn(_update_fn)

        try:
            status = await self._resolve_context(msg, pool, pool.pool_id, ctx)
        except Exception:
            log.warning(
                "_resolve_context failed — continuing with active session",
                exc_info=True,
            )
            status = ResumeStatus.SKIPPED

        ctx.trace(
            "outbound",
            "message_submitted",
            adapter=msg.platform,
            resume_status=status.value,
        )
        ctx.emit(
            PoolSubmitted(
                msg_id=msg.id,
                stage=type(self).__name__,
                pool_id=pool.pool_id,
                agent_name=ctx.binding.agent_name if ctx.binding else "",
                resume_status=status.value,
            )
        )

        if status == ResumeStatus.FRESH:
            await self._notify_session_fallthrough(msg, ctx)

        return PipelineResult(action=Action.SUBMIT_TO_POOL, pool=pool)

    async def _resolve_context(  # noqa: C901
        self,
        msg: InboundMessage,
        pool: Pool,
        pool_id: str,
        ctx: PipelineContext,
    ) -> ResumeStatus:
        """Attempt session resume before pool.submit().

        Three paths (priority order): (1) reply-to-resume,
        (2) thread-session-resume, (3) last-active-session from TurnStore.

        Returns:
            RESUMED  — a session was successfully resumed via any path.
            FRESH    — Path 2 was attempted but rejected (session pruned /
                       invalid / expired); Claude will start fresh. The
                       caller should notify the user.
            SKIPPED  — no resume was attempted (pool busy, group chat,
                       first use, no TurnStore, …). Silent and expected.
        """
        hub = ctx.hub
        path2_attempted = False

        # Path 1: reply-to-resume via MessageIndex (#341).
        if msg.reply_to_id is not None and hub._message_index is None:
            log.debug("reply-to-resume: no MessageIndex configured — skipping")
        if msg.reply_to_id is not None and hub._message_index is not None:
            session_id = await hub._message_index.resolve(pool_id, str(msg.reply_to_id))
            if session_id is not None:
                if not pool.is_idle:
                    pool._pending_session_id = session_id  # was: log + skip
                    log.info(
                        "reply-to-resume: pool %r busy — queued session %r",
                        pool_id,
                        session_id,
                    )
                else:
                    log.info(
                        "reply-to-resume: resuming session %r for pool %r",
                        session_id,
                        pool_id,
                    )
                    accepted = await pool.resume_session(session_id)
                    if accepted and hub._turn_store is not None:
                        await hub._turn_store.increment_resume_count(session_id)
                    return ResumeStatus.RESUMED

        # Path 2: thread-session-resume.
        thread_session_id: str | None = msg.platform_meta.get("thread_session_id")
        if thread_session_id is not None:
            # Scope-validate: session must belong to this pool (#525).
            if hub._turn_store is not None:
                session_pool = await hub._turn_store.get_session_pool_id(
                    thread_session_id
                )
                if session_pool is None or session_pool != pool_id:
                    log.warning(
                        "thread-session-resume: scope mismatch for %r — "
                        "expected pool %r, got %r — skipping",
                        thread_session_id,
                        pool_id,
                        session_pool,
                    )
                    return ResumeStatus.SKIPPED
            else:
                # No TurnStore → cannot validate scope → safe default: skip.
                log.debug(
                    "thread-session-resume: no TurnStore — skipping %r",
                    thread_session_id,
                )
                return ResumeStatus.SKIPPED
            if not pool.is_idle:
                log.info(
                    "thread-session-resume: pool %r busy — skipping %r",
                    pool_id,
                    thread_session_id,
                )
                return ResumeStatus.SKIPPED
            log.info(
                "thread-session-resume: resuming %r for pool %r",
                thread_session_id,
                pool_id,
            )
            path2_attempted = True
            accepted = await pool.resume_session(thread_session_id)
            if accepted:
                await hub._turn_store.increment_resume_count(thread_session_id)
                return ResumeStatus.RESUMED
            log.info(
                "thread-session-resume: session %r not accepted"
                " — falling through to Path 3",
                thread_session_id,
            )

        # Path 3: last-active-session.
        if pool.is_idle and hub._turn_store is not None:
            last_sid = await hub._turn_store.get_last_session(pool_id)
            if last_sid is None:
                log.debug(
                    "last-session-resume: no prior session for pool %r",
                    pool_id,
                )
            elif last_sid == pool.session_id:
                _agent = hub.agent_registry.get(pool.agent_name)
                _alive = (
                    _agent.is_backend_alive(pool.pool_id)
                    if _agent is not None
                    else True
                )
                if _alive:
                    log.debug(
                        "last-session-resume: pool %r already on session %r",
                        pool_id,
                        last_sid,
                    )
                    return ResumeStatus.SKIPPED
                log.warning(
                    "last-session-resume: pool %r session %r matches"
                    " but backend is dead — skipping guard",
                    pool_id,
                    last_sid,
                )
            else:
                log.info(
                    "last-session-resume: resuming %r for pool %r",
                    last_sid,
                    pool_id,
                )
                accepted = await pool.resume_session(last_sid)
                if accepted:
                    await hub._turn_store.increment_resume_count(last_sid)
                return ResumeStatus.RESUMED

        return ResumeStatus.FRESH if path2_attempted else ResumeStatus.SKIPPED

    async def _notify_session_fallthrough(
        self, msg: InboundMessage, ctx: PipelineContext
    ) -> None:
        """Send a pre-response notice when Path 2 resume fails."""
        from .outbound_errors import try_notify_user

        try:
            platform = Platform(msg.platform)
        except ValueError:
            return
        adapter = ctx.hub.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            return
        circuit = (
            ctx.hub.circuit_registry.get(msg.platform)
            if ctx.hub.circuit_registry is not None
            else None
        )
        await try_notify_user(
            msg.platform,
            adapter,
            msg,
            _SESSION_FALLTHROUGH_MSG,
            circuit=circuit,
        )
