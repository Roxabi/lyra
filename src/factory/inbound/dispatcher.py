"""Dispatcher — orchestrates typing + push_to_hub_guarded + on_drop."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from factory.core.messaging.message import Platform
from factory.core.messaging.push_guard import PushGuardDeps, push_to_hub_guarded

if TYPE_CHECKING:
    from factory.core.messaging.message import InboundMessage
    from factory.inbound.context import DispatchCtx

log = logging.getLogger(__name__)


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
        # Finding #3: guard the Platform enum lookup before push_to_hub_guarded
        # is called — an unregistered or misspelled platform value raises KeyError
        # here, before the guard in push_guard.py has any chance to intercept it.
        try:
            platform = Platform[msg.platform.upper()]
        except KeyError:
            log.warning(
                "dispatcher_unknown_platform",
                extra={
                    "platform": msg.platform,
                    "user_id": msg.user_id,
                    "dropped": True,
                },
            )
            if on_drop is not None:
                on_drop()
            text = get_msg("backpressure_ack", "Processing your request…")
            await send_backpressure(text)
            return
        await push_to_hub_guarded(
            PushGuardDeps(
                inbound_bus=ctx.inbound_bus,
                platform=platform,
                msg=msg,
                circuit_registry=ctx.circuit_registry,
                on_drop=on_drop,
                send_backpressure=send_backpressure,
                get_msg=get_msg,
                outbound_listener=ctx.outbound_listener,
            )
        )
