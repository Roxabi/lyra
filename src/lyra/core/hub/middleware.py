"""Composable middleware pipeline for inbound message routing (#431).

Replaces the monolithic ``MessagePipeline`` class with a chain of independent
middleware objects. Each middleware either short-circuits with a ``PipelineResult``
or delegates to the next middleware via ``await next(msg, ctx)``.

The pipeline is strictly sequential — no concurrent fan-out, preserving FIFO
ordering and session resume atomicity.

Layout:
  middleware.py         — protocol, context, runner, factory
  middleware_stages.py  — stages 0–8 (trace, guards, pool creation, command dispatch)
  middleware_submit.py  — stage 9 (pool submit + session resume)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from ..agent import AgentBase
from ..messaging.message import InboundMessage
from ..pool import Pool
from .pipeline_events import (
    MessageReceived,
    PipelineEvent,
    StageCompleted,
)
from .pipeline_types import (
    DROP,
    PipelineResult,
    TraceHook,
)

if TYPE_CHECKING:
    from .event_bus import PipelineEventBus
    from .hub import Binding, Hub, RoutingKey

log = logging.getLogger(__name__)


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
    event_bus: PipelineEventBus | None = None

    def trace(self, stage: str, event: str, **payload: object) -> None:
        """Emit a trace event if a hook is registered. Never raises."""
        if self.trace_hook is None:
            return
        try:
            self.trace_hook(stage, event, **payload)
        except Exception:
            log.debug("trace_hook raised — ignoring", exc_info=True)

    def emit(self, event: PipelineEvent) -> None:
        """Emit a pipeline telemetry event. No-op if bus is None."""
        if self.event_bus is not None:
            self.event_bus.emit(event)


# Type alias for the next-middleware callback.
Next = Callable[[InboundMessage, PipelineContext], Awaitable[PipelineResult]]


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
        event_bus: PipelineEventBus | None = None,
    ) -> None:
        self._middlewares = middlewares
        self._hub = hub
        self._trace_hook = trace_hook
        self._event_bus = event_bus

    async def process(self, msg: InboundMessage) -> PipelineResult:
        """Route *msg* through the middleware chain."""
        ctx = PipelineContext(
            hub=self._hub,
            trace_hook=self._trace_hook,
            event_bus=self._event_bus,
        )

        ctx.trace(
            "inbound",
            "message_received",
            msg_id=msg.id,
            platform=msg.platform,
            user_id=msg.user_id,
        )

        ctx.emit(
            MessageReceived(
                msg_id=msg.id,
                stage="inbound",
                platform=msg.platform,
                user_id=msg.user_id,
                scope_id=msg.scope_id,
            )
        )

        async def _run(
            index: int, msg: InboundMessage, ctx: PipelineContext
        ) -> PipelineResult:
            """Run middleware at *index* with timing.

            ``StageCompleted`` fires for both pass-through and drop stages.
            For pass-through stages, ``duration_ms`` is subtree-inclusive
            (includes downstream). For drop stages, it reflects only the
            stage's own work since no downstream is called.
            """
            if index >= len(self._middlewares):
                return DROP
            mw = self._middlewares[index]
            t0 = time.monotonic()
            result = await mw(msg, ctx, lambda m, c: _run(index + 1, m, c))
            ctx.emit(
                StageCompleted(
                    msg_id=msg.id,
                    stage=type(mw).__name__,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )
            )
            return result

        return await _run(0, msg, ctx)


def build_default_pipeline(
    hub: Hub,
    *,
    trace_hook: TraceHook | None = None,
    event_bus: PipelineEventBus | None = None,
) -> MiddlewarePipeline:
    """Build the standard middleware pipeline with all 10 stages."""
    from .middleware_stages import (
        CommandMiddleware,
        CreatePoolMiddleware,
        RateLimitMiddleware,
        ResolveBindingMiddleware,
        ResolveTrustMiddleware,
        TraceMiddleware,
        TrustGuardMiddleware,
        ValidatePlatformMiddleware,
    )
    from .middleware_stt import SttMiddleware
    from .middleware_submit import SubmitToPoolMiddleware

    return MiddlewarePipeline(
        [
            TraceMiddleware(),
            ValidatePlatformMiddleware(),
            ResolveTrustMiddleware(),
            TrustGuardMiddleware(),
            RateLimitMiddleware(),
            SttMiddleware(),
            ResolveBindingMiddleware(),
            CreatePoolMiddleware(),
            CommandMiddleware(),
            SubmitToPoolMiddleware(),
        ],
        hub,
        trace_hook=trace_hook,
        event_bus=event_bus,
    )
