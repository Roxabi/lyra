"""push_to_hub_guarded — circuit-open + backpressure guard for hub inbound bus.

Relocated from factory.adapters.shared._shared to factory.core.messaging so
that factory.inbound can import it without violating the inbound-no-adapters
importlinter contract (ADR-073 / #1666).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from factory.core.messaging.message import InboundMessage, Platform

if TYPE_CHECKING:
    from factory.core.lifecycle.circuit_breaker import CircuitRegistry
    from factory.core.messaging.bus import Bus
    from factory.core.ports.outbound_listener import OutboundListener

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushGuardDeps:
    """Frozen deps for push_to_hub_guarded.

    Each field is a distinct guard/callback dependency.
    """

    inbound_bus: "Bus[Any]"
    platform: Platform
    msg: InboundMessage
    circuit_registry: "CircuitRegistry | None"
    on_drop: Callable[[], None] | None
    send_backpressure: Callable[[str], Awaitable[None]]
    get_msg: Callable[[str, str], str]
    outbound_listener: "OutboundListener | None" = field(default=None)


async def push_to_hub_guarded(deps: PushGuardDeps) -> None:
    """Put *msg* on the inbound bus with circuit-open and backpressure guards.

    *on_drop* is called before early return in both circuit-open and QueueFull
    cases. *send_backpressure* sends the backpressure ack to the user.
    Always returns normally.

    *outbound_listener* — when provided, ``cache_inbound(msg)`` is called
    before enqueuing so that outbound NATS correlation can resolve the original
    message by stream_id.  Must be called here (not by the caller) to guarantee
    the cache is populated before the hub can dispatch a response.
    """
    if deps.outbound_listener is not None:
        deps.outbound_listener.cache_inbound(deps.msg)

    if deps.circuit_registry is not None:
        cb = deps.circuit_registry.get("hub")
        if cb is not None and cb.is_open():
            log.warning(
                "hub_circuit_open",
                extra={
                    "platform": deps.platform.value,
                    "user_id": deps.msg.user_id,
                    "dropped": True,
                },
            )
            if deps.on_drop is not None:
                deps.on_drop()
            text = deps.get_msg(
                "circuit_open_ack",
                "I'm temporarily overloaded, please try again in a moment.",
            )
            await deps.send_backpressure(text)
            return

    try:
        await deps.inbound_bus.put(deps.platform, deps.msg)
    except asyncio.QueueFull:
        if deps.on_drop is not None:
            deps.on_drop()
        text = deps.get_msg("backpressure_ack", "Processing your request…")
        await deps.send_backpressure(text)
    except KeyError:
        # Finding #3: Bus.put raises KeyError for an unregistered platform.
        # Treat it the same as QueueFull so this function truly always returns
        # normally (as the docstring guarantees).
        log.warning(
            "push_guard_unregistered_platform",
            extra={
                "platform": deps.platform.value,
                "user_id": deps.msg.user_id,
                "dropped": True,
            },
        )
        if deps.on_drop is not None:
            deps.on_drop()
        text = deps.get_msg("backpressure_ack", "Processing your request…")
        await deps.send_backpressure(text)
