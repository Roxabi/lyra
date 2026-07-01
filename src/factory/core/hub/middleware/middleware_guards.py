"""Early middleware stages (0–3): trace, platform validation, identity, rate limit."""

from __future__ import annotations

import logging

from factory.obs.hub_tracer import hub_ingress_span

from ...auth.trust import TrustLevel
from ...messaging.message import InboundMessage, Platform
from ...trace import TraceContext
from ..pipeline.pipeline_events import MessageDropped
from ..pipeline.pipeline_types import DROP, Action, PipelineResult
from .middleware import Next, PipelineContext

log = logging.getLogger(__name__)


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
            with hub_ingress_span(
                platform=msg.platform,
                bot_id=msg.bot_id,
                msg_id=msg.id,
            ):
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


class ResolveIdentityMiddleware:
    """Stage 2: hub-side identity resolution (C3) — ban list + admin flag.

    Adapters forward ``trust_level=PUBLIC``. This stage re-resolves identity via
    the Authenticator (BLOCKED ban check, ``is_admin`` for operator bypass) and
    drops BLOCKED users before binding or agent authorization (ADR-090).
    """

    async def __call__(
        self, msg: InboundMessage, ctx: PipelineContext, next: Next
    ) -> PipelineResult:
        msg = ctx.hub._resolve_message_trust(msg)
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
    """Stage 3: drop messages that exceed the per-user rate limit."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        from ..hub import (
            RoutingKey,  # noqa: PLC0415  — DEBT:plc0415-deferred-import# justified: .hub cycle
        )

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
