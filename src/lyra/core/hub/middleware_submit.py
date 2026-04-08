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
from .middleware import Next, PipelineContext
from .pipeline_events import MessageDropped, PoolSubmitted
from .pipeline_types import (
    DROP,
    SESSION_FALLTHROUGH_MSG,
    Action,
    PipelineResult,
    ResumeStatus,
)

log = logging.getLogger(__name__)


class SubmitToPoolMiddleware:
    """Stage 9 (index 9, terminal): validate adapter, circuit breaker, submit."""

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
            return DROP

        if await ctx.hub.circuit_breaker_drop(msg):
            ctx.trace("outbound", "circuit_open", action=Action.DROP.value)
            ctx.emit(
                MessageDropped(
                    msg_id=msg.id,
                    stage=type(self).__name__,
                    reason="circuit_open",
                )
            )
            return DROP

        pool = ctx.pool
        # Register session persistence callback once.
        _update_fn = msg.platform_meta.get("_session_update_fn")
        if callable(_update_fn) and pool._observer._session_update_fn is None:
            pool._observer.register_session_update_fn(_update_fn)  # type: ignore[arg-type]

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

    async def _resolve_context(
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
        result = await self._resume_path1(msg, pool, pool_id, ctx)
        if result is not None:
            return result

        path2_result, path2_attempted = await self._resume_path2(
            msg, pool, pool_id, ctx
        )
        if path2_result is not None:
            return path2_result

        result = await self._resume_path3(msg, pool, pool_id, ctx)
        if result is not None:
            return result

        return ResumeStatus.FRESH if path2_attempted else ResumeStatus.SKIPPED

    async def _resume_path1(
        self,
        msg: InboundMessage,
        pool: Pool,
        pool_id: str,
        ctx: PipelineContext,
    ) -> ResumeStatus | None:
        """Path 1: reply-to-resume via MessageIndex (#341). None = not applicable."""
        hub = ctx.hub
        if msg.reply_to_id is None:
            return None
        if hub._message_index is None:
            log.debug("reply-to-resume: no MessageIndex configured — skipping")
            return None
        session_id = await hub._message_index.resolve(pool_id, str(msg.reply_to_id))
        if session_id is None:
            return None
        if not pool.is_idle:
            pool._pending_session_id = session_id  # was: log + skip
            log.info(
                "reply-to-resume: pool %r busy — queued session %r",
                pool_id,
                session_id,
            )
            return ResumeStatus.SKIPPED
        log.info(
            "reply-to-resume: resuming session %r for pool %r",
            session_id,
            pool_id,
        )
        await pool.resume_session(session_id)
        return ResumeStatus.RESUMED

    async def _resume_path2(
        self,
        msg: InboundMessage,
        pool: Pool,
        pool_id: str,
        ctx: PipelineContext,
    ) -> tuple[ResumeStatus | None, bool]:
        """Path 2: thread-session-resume. Returns (status|None, attempted)."""
        hub = ctx.hub
        thread_session_id: str | None = msg.platform_meta.get("thread_session_id")
        if thread_session_id is None:
            return None, False

        # Scope-validate: session must belong to this pool (#525).
        if hub._turn_store is None:
            # No TurnStore → cannot validate scope → safe default: skip.
            log.debug(
                "thread-session-resume: no TurnStore — skipping %r",
                thread_session_id,
            )
            return ResumeStatus.SKIPPED, False

        session_pool = await hub._turn_store.get_session_pool_id(thread_session_id)
        if session_pool is None or session_pool != pool_id:
            log.warning(
                "thread-session-resume: scope mismatch for %r — "
                "expected pool %r, got %r — skipping",
                thread_session_id,
                pool_id,
                session_pool,
            )
            return ResumeStatus.SKIPPED, False

        if not pool.is_idle:
            log.info(
                "thread-session-resume: pool %r busy — skipping %r",
                pool_id,
                thread_session_id,
            )
            return ResumeStatus.SKIPPED, False

        if thread_session_id == pool.session_id:
            log.debug(
                "thread-session-resume: pool %r already on session %r — skipping",
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
            "thread-session-resume: session %r not accepted"
            " — falling through to Path 3",
            thread_session_id,
        )
        return None, True

    async def _resume_path3(
        self,
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
            _alive = (
                _agent.is_backend_alive(pool.pool_id) if _agent is not None else True
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
            return None

        log.info(
            "last-session-resume: resuming %r for pool %r",
            last_sid,
            pool_id,
        )
        await pool.resume_session(last_sid)
        return ResumeStatus.RESUMED

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
            SESSION_FALLTHROUGH_MSG,
            circuit=circuit,
        )
