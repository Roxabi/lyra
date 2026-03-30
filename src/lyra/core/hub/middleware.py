"""Composable middleware pipeline for inbound message routing (#431).

Replaces the monolithic ``MessagePipeline`` class with a chain of independent
middleware objects. Each middleware either short-circuits with a ``PipelineResult``
or delegates to the next middleware via ``await next(msg, ctx)``.

The pipeline is strictly sequential — no concurrent fan-out, preserving FIFO
ordering and session resume atomicity.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..agent import AgentBase
from ..commands.command_parser import CommandParser
from ..message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    Platform,
    Response,
)
from ..pool import Pool
from .message_pipeline import (
    Action,
    PipelineResult,
    ResumeStatus,
    TraceHook,
    _SESSION_FALLTHROUGH_MSG,
)

if TYPE_CHECKING:
    from .hub import Binding, Hub, RoutingKey

log = logging.getLogger(__name__)

_command_parser = CommandParser()

_DROP = PipelineResult(action=Action.DROP)


@dataclass
class PipelineContext:
    """Mutable routing context accumulated as middleware runs."""

    hub: Hub
    key: RoutingKey | None = None
    binding: Binding | None = None
    agent: AgentBase | None = None
    pool: Pool | None = None
    router: Any = None
    trace_hook: TraceHook | None = None

    def trace(self, stage: str, event: str, **payload: object) -> None:
        """Emit a trace event if a hook is registered. Never raises."""
        if self.trace_hook is None:
            return
        try:
            self.trace_hook(stage, event, **payload)
        except Exception:
            log.debug("trace_hook raised — ignoring", exc_info=True)


# Type alias for the next-middleware callback (defined after PipelineContext).
Next = Callable[[InboundMessage, PipelineContext], Awaitable[PipelineResult]]


@runtime_checkable
class PipelineMiddleware(Protocol):
    """One stage of the inbound message pipeline.

    Return a ``PipelineResult`` to short-circuit (DROP / COMMAND_HANDLED),
    or call ``await next(msg, ctx)`` to pass to the next middleware.
    """

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult: ...


class MiddlewarePipeline:
    """Runs a list of middleware sequentially. Replaces MessagePipeline."""

    def __init__(
        self,
        middlewares: list[PipelineMiddleware],
        hub: Hub,
        *,
        trace_hook: TraceHook | None = None,
    ) -> None:
        self._middlewares = middlewares
        self._hub = hub
        self._trace_hook = trace_hook

    async def process(self, msg: InboundMessage) -> PipelineResult:
        """Route *msg* through the middleware chain."""
        ctx = PipelineContext(hub=self._hub, trace_hook=self._trace_hook)

        ctx.trace(
            "inbound",
            "message_received",
            msg_id=msg.id,
            platform=msg.platform,
            user_id=msg.user_id,
        )

        async def _run(
            index: int, msg: InboundMessage, ctx: PipelineContext
        ) -> PipelineResult:
            if index >= len(self._middlewares):
                return _DROP
            mw = self._middlewares[index]
            return await mw(msg, ctx, lambda m, c: _run(index + 1, m, c))

        return await _run(0, msg, ctx)


# ──────────────────────────────────────────────────────────────────────
# Concrete middlewares (1:1 mapping from MessagePipeline private methods)
# ──────────────────────────────────────────────────────────────────────


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
        assert ctx.key is not None

        binding = ctx.hub.resolve_binding(msg)
        if binding is None:
            log.warning("unmatched routing key %s — message dropped", ctx.key)
            ctx.trace("pool", "no_binding", action=Action.DROP.value)
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
            return _DROP
        ctx.agent = agent

        return await next(msg, ctx)


class CreatePoolMiddleware:
    """Stage 4: get or create the conversation pool. Sets ctx.pool, ctx.router."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        assert ctx.binding is not None
        assert ctx.agent is not None

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
        assert ctx.pool is not None
        assert ctx.key is not None

        router = ctx.router
        if router and router.is_command(msg):
            _cmd = msg.text.split()[0] if msg.text else ""
            ctx.trace("processor", "command_detected", command=_cmd)
            return await self._dispatch_command(msg, router, ctx.pool, ctx.key, ctx, next)

        return await next(msg, ctx)

    async def _dispatch_command(
        self,
        msg: InboundMessage,
        router: Any,
        pool: Pool,
        key: RoutingKey,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        _agent = ctx.hub.agent_registry.get(pool.agent_name)
        if _agent is not None and hasattr(_agent, "configure_pool"):
            _agent.configure_pool(pool)
        try:
            response = await router.dispatch(msg, pool)
        except Exception as exc:
            log.exception("command dispatch failed for %s: %s", key, exc)
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
            return PipelineResult(action=Action.COMMAND_HANDLED, response=response)

        if response is None:  # !-prefixed command not found — fall through
            ctx.trace("processor", "command_fallthrough")
            return await next(msg, ctx)

        ctx.trace("outbound", "command_handled", action=Action.COMMAND_HANDLED.value)
        return PipelineResult(action=Action.COMMAND_HANDLED, response=response)


class SubmitToPoolMiddleware:
    """Stage 6 (terminal): validate adapter, check circuit breaker, submit."""

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        assert ctx.pool is not None
        assert ctx.key is not None

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
            return _DROP

        if await ctx.hub.circuit_breaker_drop(msg):
            ctx.trace("outbound", "circuit_open", action=Action.DROP.value)
            return _DROP

        pool = ctx.pool
        # Register session persistence callback once.
        _update_fn = msg.platform_meta.get("_session_update_fn")
        if _update_fn is not None and pool._observer._session_update_fn is None:
            pool._observer.register_session_update_fn(_update_fn)

        # Wire provider callbacks before _resolve_context.
        _agent = ctx.hub.agent_registry.get(pool.agent_name)
        if _agent is not None and hasattr(_agent, "configure_pool"):
            _agent.configure_pool(pool)

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

        Three paths (priority order): (1) reply-to-resume, (2) thread-session-resume,
        (3) last-active-session from TurnStore.
        """
        hub = ctx.hub
        path2_attempted = False

        # Path 1: reply-to-resume via MessageIndex (#341).
        if msg.reply_to_id is not None and hub._message_index is None:
            log.debug("reply-to-resume: no MessageIndex configured — skipping")
        if msg.reply_to_id is not None and hub._message_index is not None:
            session_id = await hub._message_index.resolve(
                pool_id, str(msg.reply_to_id)
            )
            if session_id is not None:
                if not pool.is_idle:
                    log.info(
                        "reply-to-resume: pool %r busy — skipping resume of session %r",
                        pool_id,
                        session_id,
                    )
                else:
                    log.info(
                        "reply-to-resume: resuming session %r for pool %r",
                        session_id,
                        pool_id,
                    )
                    await pool.resume_session(session_id)
                    return ResumeStatus.RESUMED

        # Path 2: thread-session-resume.
        thread_session_id: str | None = msg.platform_meta.get("thread_session_id")
        if thread_session_id is not None:
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
                await pool.resume_session(last_sid)
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
            msg.platform, adapter, msg, _SESSION_FALLTHROUGH_MSG, circuit=circuit
        )


# ──────────────────────────────────────────────────────────────────────
# Factory — default middleware stack
# ──────────────────────────────────────────────────────────────────────


def build_default_pipeline(
    hub: Hub,
    *,
    trace_hook: TraceHook | None = None,
) -> MiddlewarePipeline:
    """Build the standard middleware pipeline with all 6 stages."""
    return MiddlewarePipeline(
        [
            ValidatePlatformMiddleware(),
            RateLimitMiddleware(),
            ResolveBindingMiddleware(),
            CreatePoolMiddleware(),
            CommandMiddleware(),
            SubmitToPoolMiddleware(),
        ],
        hub,
        trace_hook=trace_hook,
    )
