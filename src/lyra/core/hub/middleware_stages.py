"""Concrete middleware stages for the inbound message pipeline (#431).

Stages 0–8 (trace, guards, pool creation, command dispatch); stage 9
(pool submit + session resume) lives in ``middleware_submit.py``.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from ..commands.command_parser import CommandParser
from ..message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    Platform,
    Response,
)
from ..trace import TraceContext
from ..trust import TrustLevel
from .middleware import Next, PipelineContext
from .pipeline_events import CommandDispatched, MessageDropped
from .pipeline_types import (
    DROP,
    Action,
    PipelineResult,
)

log = logging.getLogger(__name__)

_command_parser = CommandParser()


class TraceMiddleware:
    """Stage 0: generate a per-turn trace_id and store it in contextvars (#270)."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        token = TraceContext.set_trace_id(TraceContext.generate())
        try:
            return await next(msg, ctx)
        finally:
            TraceContext.reset_trace_id(token)


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
                    msg_id=msg.id, stage=type(self).__name__, reason="unknown_platform"
                )
            )
            return DROP
        return await next(msg, ctx)


class ResolveTrustMiddleware:
    """Stage 2: resolve trust level from Hub authenticator (C3).

    Must precede TrustGuardMiddleware.
    """

    async def __call__(
        self, msg: InboundMessage, ctx: PipelineContext, next: Next
    ) -> PipelineResult:
        msg = ctx.hub._resolve_message_trust(msg)
        return await next(msg, ctx)


class TrustGuardMiddleware:
    """Stage 3: drop BLOCKED users (C3). Trust resolved by ResolveTrustMiddleware."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        if msg.trust_level == TrustLevel.BLOCKED:
            log.info(
                "trust_blocked user=%s platform=%s — message dropped",
                msg.user_id,
                msg.platform,
            )
            ctx.emit(
                MessageDropped(
                    msg_id=msg.id, stage=type(self).__name__, reason="trust_blocked"
                )
            )
            return DROP
        return await next(msg, ctx)


class RateLimitMiddleware:
    """Stage 4: drop messages that exceed the per-user rate limit."""

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
                    msg_id=msg.id, stage=type(self).__name__, reason="rate_limited"
                )
            )
            return DROP
        return await next(msg, ctx)


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
            and not getattr(pool, "_configured", False)
        ):
            _agent.configure_pool(pool)
            pool._configured = True  # type: ignore[attr-defined]

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
