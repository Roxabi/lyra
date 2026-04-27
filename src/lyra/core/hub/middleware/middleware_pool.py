"""Pool setup and command middleware stages (6–8)."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from ...commands.command_parser import CommandParser
from ...messaging.message import GENERIC_ERROR_REPLY, InboundMessage, Response
from ...trace import TraceContext
from ..pipeline.pipeline_events import CommandDispatched, MessageDropped
from ..pipeline.pipeline_types import DROP, Action, PipelineResult
from .middleware import Next, PipelineContext

log = logging.getLogger(__name__)
_command_parser = CommandParser()


class ResolveBindingMiddleware:
    """Stage 6: resolve binding + look up agent. Sets ctx.binding, ctx.agent."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        if ctx.key is None:
            raise RuntimeError(
                "RateLimitMiddleware must precede ResolveBindingMiddleware"
            )

        binding = ctx.hub.resolve_binding(msg)
        if binding is None:
            log.warning("unmatched routing key %s — message dropped", ctx.key)
            ctx.trace("pool", "no_binding", action=Action.DROP.value)
            ctx.emit(
                MessageDropped(
                    msg_id=msg.id, stage=type(self).__name__, reason="no_binding"
                )
            )
            return DROP
        ctx.binding = binding
        agent = ctx.hub.agent_registry.get(binding.agent_name)
        if agent is None:
            log.warning(
                "no agent registered for %r (routing %s) — message dropped",
                binding.agent_name,
                ctx.key,
            )
            ctx.trace(
                "pool",
                "no_agent",
                agent_name=binding.agent_name,
                action=Action.DROP.value,
            )
            ctx.emit(
                MessageDropped(
                    msg_id=msg.id, stage=type(self).__name__, reason="no_agent"
                )
            )
            return DROP
        ctx.agent = agent
        return await next(msg, ctx)


class CreatePoolMiddleware:
    """Stage 7: get or create the pool. Sets ctx.pool, ctx.router."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        if ctx.binding is None:
            raise RuntimeError(
                "ResolveBindingMiddleware must precede CreatePoolMiddleware"
            )
        if ctx.agent is None:
            raise RuntimeError(
                "ResolveBindingMiddleware must set ctx.agent"
                " before CreatePoolMiddleware"
            )

        pool = ctx.hub.get_or_create_pool(
            ctx.binding.pool_id,
            ctx.binding.agent_name,
        )
        ctx.pool = pool
        pool_id_token = TraceContext.set_pool_id(ctx.binding.pool_id)
        ctx.trace(
            "pool",
            "agent_selected",
            agent=ctx.binding.agent_name,
            pool_id=ctx.binding.pool_id,
        )

        ctx.router = getattr(ctx.agent, "command_router", None)

        # Wire provider callbacks once at pool creation.
        _agent = ctx.hub.agent_registry.get(pool.agent_name)
        if (
            _agent is not None
            and hasattr(_agent, "configure_pool")
            and not pool._configured
        ):
            _agent.configure_pool(pool)
            pool._configured = True
            # configure_pool must be idempotent — no lock guards this check-then-set

        if pool._on_resume_fn is None and ctx.hub._turn_store is not None:  # #597
            pool._on_resume_fn = ctx.hub._turn_store.increment_resume_count
        # Parse command context and rewrite bare URLs (#99).
        cmd_ctx = _command_parser.parse(msg.text)
        if cmd_ctx is not None:
            msg = dataclasses.replace(msg, command=cmd_ctx)

        if ctx.router and hasattr(ctx.router, "prepare"):
            msg = ctx.router.prepare(msg)
        try:
            return await next(msg, ctx)
        finally:
            TraceContext.reset_pool_id(pool_id_token)


class CommandMiddleware:
    """Stage 8: detect and dispatch commands."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        if ctx.pool is None:
            raise RuntimeError("CreatePoolMiddleware must precede CommandMiddleware")
        if ctx.key is None:
            raise RuntimeError("RateLimitMiddleware must precede CommandMiddleware")
        router = ctx.router
        if router and router.is_command(msg):
            _cmd = msg.text.split()[0] if msg.text else ""
            ctx.trace("processor", "command_detected", command=_cmd)
            return await self._dispatch_command(msg, _cmd, router, ctx, next)
        return await next(msg, ctx)

    async def _dispatch_command(  # noqa: PLR0913
        self,
        msg: InboundMessage,
        cmd: str,
        router: Any,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        pool = ctx.pool
        key = ctx.key
        try:
            response = await router.dispatch(msg, pool)
        except Exception as exc:
            log.exception("command dispatch failed for %s: %s", key, exc)
            mgr = ctx.hub._msg_manager
            _content = mgr.get("generic") if mgr else GENERIC_ERROR_REPLY
            ctx.trace("outbound", "command_error", action=Action.COMMAND_HANDLED.value)
            return PipelineResult(
                action=Action.COMMAND_HANDLED, response=Response(content=_content)
            )

        if response is None:
            ctx.trace("processor", "command_fallthrough")
            return await next(msg, ctx)
        ctx.trace("outbound", "command_handled", action=Action.COMMAND_HANDLED.value)
        ctx.emit(
            CommandDispatched(msg_id=msg.id, stage=type(self).__name__, command=cmd)
        )
        return PipelineResult(action=Action.COMMAND_HANDLED, response=response)
