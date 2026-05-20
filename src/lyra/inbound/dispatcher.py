"""Dispatcher — orchestrates typing + push_to_hub_guarded + on_drop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from lyra.adapters.shared._shared import push_to_hub_guarded
from lyra.core.messaging.message import Platform

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.inbound.context import DispatchCtx


def _default_get_msg(key: str, display_fallback: str = "") -> str:
    """Return *display_fallback* when no catalog is available, else *key*.

    Unlike ``MessageManager.get(key, fallback)``, there is no catalog lookup
    here — the no-catalog case has no message store.  Call sites always pass a
    real user-visible string as *display_fallback*; *key* (an identifier such as
    ``circuit_open_ack``) is the last resort when no fallback was given.
    """
    return display_fallback or key


class Dispatcher:
    """Tail stage of the inbound pipeline.

    Enqueues the fully-built ``InboundMessage`` onto the inbound bus via
    ``push_to_hub_guarded``.  Handles circuit-open and QueueFull backpressure
    by invoking ``send_backpressure`` and optionally ``on_drop`` (e.g. to
    cancel a typing indicator).

    ``send_backpressure`` is passed per-call (not held in context) because its
    closure captures the raw platform message reference.
    """

    async def dispatch(
        self,
        msg: InboundMessage,
        ctx: DispatchCtx,
        send_backpressure: Callable[[str], Awaitable[None]],
        on_drop: Callable[[], None] | None = None,
    ) -> None:
        """Dispatch *msg* to the hub bus with backpressure and drop guards."""
        _catalog = ctx.msg_catalog

        def _catalog_get_msg(key: str, fallback: str) -> str:
            looked_up = _catalog.get(key) if _catalog is not None else None
            return looked_up or fallback or key

        get_msg: Callable[[str, str], str] = (
            _catalog_get_msg if _catalog is not None else _default_get_msg
        )
        await push_to_hub_guarded(
            inbound_bus=ctx.inbound_bus,
            platform=Platform[msg.platform.upper()],
            msg=msg,
            circuit_registry=ctx.circuit_registry,
            on_drop=on_drop,
            send_backpressure=send_backpressure,
            get_msg=get_msg,
            outbound_listener=ctx.outbound_listener,
        )
