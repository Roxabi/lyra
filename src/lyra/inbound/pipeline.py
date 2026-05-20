"""InboundPipeline — orchestrates parse → route → session → dispatch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.inbound.context import InboundContext
    from lyra.inbound.wire_parser import WireParser

from lyra.inbound.dispatcher import Dispatcher
from lyra.inbound.router import Router
from lyra.inbound.session_builder import SessionBuilder


class InboundPipeline:
    """Orchestrates the full inbound message pipeline.

    Pipeline shape (resolved NC2 + NC3):

        parse → pre_route_hook(opt) → Router → [DROP → return]
              → pre_session_hook(opt) → SessionBuilder → Dispatcher

    Args:
        router: ``Router`` instance.  Defaults to a fresh ``Router()``.
        session_builder: ``SessionBuilder`` instance.  Defaults to a fresh one.
        dispatcher: ``Dispatcher`` instance.  Defaults to a fresh one.

    Stub: full logic implemented in Wave 5 (Slice 5).
    """

    def __init__(
        self,
        router: Router | None = None,
        session_builder: SessionBuilder | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self._router = router or Router()
        self._session_builder = session_builder or SessionBuilder()
        self._dispatcher = dispatcher or Dispatcher()

    async def run(  # noqa: PLR0913 — pipeline signature; each param is a distinct stage hook
        self,
        raw: Any,
        ctx: InboundContext,
        parser: WireParser,
        *,
        pre_route_hook: Callable[..., Awaitable[None]] | None = None,
        pre_session_hook: Callable[..., Awaitable[InboundMessage]] | None = None,
        send_backpressure: Callable[[str], Awaitable[None]],
        on_drop: Callable[[], None] | None = None,
    ) -> None:
        """Run the pipeline from raw platform event to hub enqueue.

        Args:
            raw: Raw platform event (aiogram ``Message``, discord.py ``Message``).
            ctx: Composite inbound context (router + session + dispatch sub-contexts).
            parser: Platform-specific ``WireParser`` implementation.
            pre_route_hook: Optional async hook called after parse, before
                ``Router.decide``.  Discord supplies a hook that performs cold-path
                ``ThreadStore.is_owned`` warmup and mutates
                ``ctx.router.owned_threads``.
            pre_session_hook: Optional async hook called after
                ``Router.decide=PROCESS``, before ``SessionBuilder.build``.
                Discord supplies a hook for auto-thread creation and claim;
                it RETURNS the (possibly updated) ``InboundMessage`` so
                ``DiscordMeta.thread_id`` propagates downstream.
            send_backpressure: Async callable that sends a backpressure ack to the
                user.  Passed per-call because its closure captures the raw message.
            on_drop: Optional sync callable invoked when the message is dropped
                (circuit-open or QueueFull).  Typically cancels a typing indicator.
        """
        raise NotImplementedError
