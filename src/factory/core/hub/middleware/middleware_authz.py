"""Agent-scoped authorization stage (7) — ADR-090 §5.

Once ``ResolveBindingMiddleware`` has resolved the bound agent, ask the
``AgentAuthorizer`` port whether the inbound principal (the user, or any of their
roles) holds a ``use`` grant on that agent. Authorized traffic passes through
unchanged; an unauthorized principal is refused **by pull** — an inline reply on
the originating channel pointing to the public surface, never a pushed DM — and a
``MessageDropped(reason="agent_unauthorized")`` audit event is emitted.

The stage depends only on the ``AgentAuthorizer`` Protocol (``core/auth``); the
SQLite ``AgentGrantStore`` is injected as a port reference at wiring time
(ADR-059). ``authorize`` is synchronous — a warm cache read, never awaited.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ...messaging.message import InboundMessage, Response
from ..pipeline.pipeline_events import MessageDropped
from ..pipeline.pipeline_types import Action, PipelineResult
from .middleware import Next, PipelineContext

if TYPE_CHECKING:
    from ...auth.agent_grants import AgentAuthorizer

log = logging.getLogger(__name__)

# Generic public surface used when the bound bot declares no ``public_bot``
# handle — the refusal degrades to this pointer (ADR-090 §5).
_DEFAULT_PUBLIC_SURFACE = "factory.roxabi.dev"

# Static refusal when no MessageManager is wired (tests) or the key is missing.
_FALLBACK_REFUSAL = (
    f"Access not authorized for this agent — visit {_DEFAULT_PUBLIC_SURFACE}"
)


class AuthorizeAgentMiddleware:
    """Stage 7 (ADR-090 §5): refuse principals lacking a USE grant on the agent.

    Runs after ``ResolveBindingMiddleware`` (agent known) and before
    ``MessagePrepMiddleware``. Deny-safe: if binding/agent is unresolved, or no
    authorizer is wired, it passes through — ``ResolveBindingMiddleware`` already
    drops unbindable messages upstream, and an unwired authorizer is a
    test/back-compat seam (production injects the live ``AgentGrantStore``).
    """

    def __init__(self, authorizer: AgentAuthorizer | None = None) -> None:
        self._authorizer = authorizer

    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        if ctx.binding is None or ctx.agent is None:
            return await next(msg, ctx)
        if self._authorizer is None:
            return await next(msg, ctx)

        decision = self._authorizer.authorize(
            agent_name=ctx.binding.agent_name,
            user_id=msg.user_id,
            roles=msg.roles,
        )
        if decision.allowed:
            return await next(msg, ctx)

        log.info(
            "agent_unauthorized user=%s agent=%s — refused (%s)",
            msg.user_id,
            ctx.binding.agent_name,
            decision.reason,
        )
        ctx.trace(
            "pool",
            "agent_unauthorized",
            agent=ctx.binding.agent_name,
            action=Action.COMMAND_HANDLED.value,
        )
        ctx.emit(
            MessageDropped(
                msg_id=msg.id, stage=type(self).__name__, reason="agent_unauthorized"
            )
        )
        # ADR-090 §5 pull-model refusal: point the denied PUBLIC sender at the
        # bound bot's dedicated public bot, degrading to the generic surface when
        # none is configured. This is a pointer only — deny-by-default stands.
        public_bot = ctx.binding.public_bot or _DEFAULT_PUBLIC_SURFACE
        text = (
            ctx.hub.get_message("agent_unauthorized", public_bot=public_bot)
            or _FALLBACK_REFUSAL
        )
        return PipelineResult(
            action=Action.COMMAND_HANDLED, response=Response(content=text)
        )
