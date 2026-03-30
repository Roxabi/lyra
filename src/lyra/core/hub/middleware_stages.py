"""Concrete middleware stages for the inbound message pipeline (#431).

Each class is one stage of the pipeline, independently testable with a
mock ``next()`` callback. Stages 1–5 (guards + command dispatch) live here;
stage 6 (pool submit + session resume) lives in ``middleware_submit.py``.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from ..commands.command_parser import CommandParser
from ..message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    Platform,
    Response,
)
from .message_pipeline import (
    _DROP,
    Action,
    PipelineResult,
)
from .middleware import Next, PipelineContext
from .pipeline_events import CommandDispatched, MessageDropped

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_command_parser = CommandParser()


class ValidatePlatformMiddleware:
    """Stage 1: reject messages from unknown platforms."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        try:
            Platform(msg.platform)
        except ValueError:
            log.warning(
                "unknown platform %r in msg id=%s — message dropped",
                msg.platform,
                msg.id,
            )
            ctx.trace(
                "inbound",
                "platform_invalid",
                platform=msg.platform,
                action=Action.DROP.value,
            )
            ctx.emit(
                MessageDropped(
                    msg_id=msg.id,
                    stage=type(self).__name__,
                    reason="unknown_platform",
                )
            )
            return _DROP
        return await next(msg, ctx)


class RateLimitMiddleware:
    """Stage 2: drop messages that exceed the per-user rate limit."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        from .hub import RoutingKey  # noqa: PLC0415

        key = RoutingKey(Platform(msg.platform), msg.bot_id, msg.scope_id)
        ctx.key = key

        if ctx.hub._is_rate_limited(msg):
            log.warning("rate limit exceeded for %s — message dropped", key)
            ctx.trace("inbound", "rate_limited", action=Action.DROP.value)
            ctx.emit(
                MessageDropped(
                    msg_id=msg.id,
                    stage=type(self).__name__,
                    reason="rate_limited",
                )
            )
            return _DROP
        return await next(msg, ctx)


class ResolveBindingMiddleware:
    """Stage 3: resolve binding + look up agent. Sets ctx.binding, ctx.agent."""

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
            log.warning(
                "unmatched routing key %s — message dropped", ctx.key
            )
            ctx.trace("pool", "no_binding", action=Action.DROP.value)
            ctx.emit(
                MessageDropped(
                    msg_id=msg.id,
                    stage=type(self).__name__,
                    reason="no_binding",
                )
            )
            return _DROP
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
                    msg_id=msg.id,
                    stage=type(self).__name__,
                    reason="no_agent",
                )
            )
            return _DROP
        ctx.agent = agent

        return await next(msg, ctx)


class CreatePoolMiddleware:
    """Stage 4: get or create the pool. Sets ctx.pool, ctx.router."""

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
        ctx.trace(
            "pool",
            "agent_selected",
            agent=ctx.binding.agent_name,
            pool_id=ctx.binding.pool_id,
        )

        ctx.router = getattr(ctx.agent, "command_router", None)

        # Wire provider callbacks once at pool creation.
        _agent = ctx.hub.agent_registry.get(pool.agent_name)
        if _agent is not None and hasattr(_agent, "configure_pool"):
            _agent.configure_pool(pool)

        # Parse command context and rewrite bare URLs (#99).
        cmd_ctx = _command_parser.parse(msg.text)
        if cmd_ctx is not None:
            msg = dataclasses.replace(msg, command=cmd_ctx)

        if ctx.router and hasattr(ctx.router, "prepare"):
            msg = ctx.router.prepare(msg)

        return await next(msg, ctx)


class CommandMiddleware:
    """Stage 5: detect and dispatch commands."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        if ctx.pool is None:
            raise RuntimeError(
                "CreatePoolMiddleware must precede CommandMiddleware"
            )
        if ctx.key is None:
            raise RuntimeError(
                "RateLimitMiddleware must precede CommandMiddleware"
            )

        router = ctx.router
        if router and router.is_command(msg):
            _cmd = msg.text.split()[0] if msg.text else ""
            ctx.trace("processor", "command_detected", command=_cmd)
            ctx.emit(
                CommandDispatched(
                    msg_id=msg.id,
                    stage=type(self).__name__,
                    command=_cmd,
                )
            )
            return await self._dispatch_command(
                msg, router, ctx, next
            )

        return await next(msg, ctx)

    async def _dispatch_command(  # noqa: PLR0913
        self,
        msg: InboundMessage,
        router: Any,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        pool = ctx.pool  # guaranteed non-None by __call__ guard
        key = ctx.key  # guaranteed non-None by __call__ guard
        try:
            response = await router.dispatch(msg, pool)
        except Exception as exc:
            log.exception(
                "command dispatch failed for %s: %s", key, exc
            )
            _content = (
                ctx.hub._msg_manager.get("generic")
                if ctx.hub._msg_manager
                else GENERIC_ERROR_REPLY
            )
            response = Response(content=_content)
            ctx.trace(
                "outbound",
                "command_error",
                action=Action.COMMAND_HANDLED.value,
            )
            return PipelineResult(
                action=Action.COMMAND_HANDLED, response=response
            )

        if response is None:
            ctx.trace("processor", "command_fallthrough")
            return await next(msg, ctx)

        ctx.trace(
            "outbound",
            "command_handled",
            action=Action.COMMAND_HANDLED.value,
        )
        return PipelineResult(
            action=Action.COMMAND_HANDLED, response=response
        )
