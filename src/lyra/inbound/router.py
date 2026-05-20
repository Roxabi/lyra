"""Router — pure routing decision (PROCESS / DROP) for inbound messages."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.inbound.context import RouterCtx


class RouteDecision(Enum):
    """Routing outcome returned by ``Router.decide``."""

    PROCESS = "process"
    DROP = "drop"


class Router:
    """Decides whether an inbound message should be processed or dropped.

    Reads ``InboundMessage.platform_meta``, ``InboundMessage.is_mention``,
    ``RouterCtx.owned_threads``, and ``RouterCtx.watch_channels``.
    Sync and pure — no I/O, no side effects.

    Stub: full logic implemented in Wave 3 (Slice 3).
    """

    def decide(self, msg: InboundMessage, ctx: RouterCtx) -> RouteDecision:
        """Return ``PROCESS`` or ``DROP`` for *msg* given routing context *ctx*."""
        raise NotImplementedError
