"""Terminal middleware stage: pool submission + session resume (#431).

``SubmitToPoolMiddleware`` is the final stage in the default pipeline.
It validates the adapter, checks the circuit breaker, attempts session
resume via three paths, and returns ``SUBMIT_TO_POOL``.
"""

from __future__ import annotations

import logging

from ...messaging.message import (
    InboundMessage,
    Platform,
)
from ..pipeline.pipeline_events import MessageDropped, PoolSubmitted
from ..pipeline.pipeline_types import (
    DROP,
    SESSION_FALLTHROUGH_MSG,
    Action,
    PipelineResult,
    ResumeStatus,
)
from .middleware import Next, PipelineContext
from .path_validation import resolve_context

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
        if msg.session_update_fn is not None and not pool.has_session_update_fn():
            pool.register_session_callbacks(update_fn=msg.session_update_fn)

        try:
            status = await resolve_context(msg, pool, pool.pool_id, ctx)
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

        return PipelineResult(action=Action.SUBMIT_TO_POOL, pool=pool, msg=msg)

    async def _notify_session_fallthrough(
        self, msg: InboundMessage, ctx: PipelineContext
    ) -> None:
        """Send a pre-response notice when Path 2 resume fails."""
        from ..outbound.outbound_errors import try_notify_user

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
