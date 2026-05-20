"""Dispatcher — orchestrates typing + push_to_hub_guarded + on_drop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.inbound.context import DispatchCtx


class Dispatcher:
    """Tail stage of the inbound pipeline.

    Enqueues the fully-built ``InboundMessage`` onto the inbound bus via
    ``push_to_hub_guarded``.  Handles circuit-open and QueueFull backpressure
    by invoking ``send_backpressure`` and optionally ``on_drop`` (e.g. to
    cancel a typing indicator).

    ``send_backpressure`` is passed per-call (not held in context) because its
    closure captures the raw platform message reference.

    Stub: full logic implemented in Wave 2 (Slice 2).
    """

    async def dispatch(
        self,
        msg: InboundMessage,
        ctx: DispatchCtx,
        send_backpressure: Callable[[str], Awaitable[None]],
        on_drop: Callable[[], None] | None = None,
    ) -> None:
        """Dispatch *msg* to the hub bus with backpressure and drop guards."""
        raise NotImplementedError
