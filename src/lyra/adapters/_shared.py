"""Shared helpers for channel adapters.

Extracted from Telegram and Discord adapters to eliminate near-identical
circuit-open / backpressure guard logic and reply_to_id parsing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import InboundAudio, InboundMessage, Platform

log = logging.getLogger(__name__)


async def push_to_hub_guarded(
    *,
    inbound_bus: object,
    platform: Platform,
    msg: InboundMessage | InboundAudio,
    circuit_registry: CircuitRegistry | None,
    on_drop: Callable[[], None] | None,
    send_backpressure: Callable[[str], Awaitable[None]],
    get_msg: Callable[[str, str], str],
) -> None:
    """Put *msg* on the inbound bus with circuit-open and backpressure guards.

    *on_drop* is called before early return in both circuit-open and QueueFull
    cases. *send_backpressure* sends the backpressure ack to the user.
    Always returns normally.
    """
    if circuit_registry is not None:
        cb = circuit_registry.get("hub")
        if cb is not None and cb.is_open():
            log.warning(
                "hub_circuit_open",
                extra={
                    "platform": platform.value,
                    "user_id": msg.user_id,
                    "dropped": True,
                },
            )
            if on_drop is not None:
                on_drop()
            return

    try:
        inbound_bus.put(platform, msg)  # type: ignore[attr-defined]
    except asyncio.QueueFull:
        if on_drop is not None:
            on_drop()
        text = get_msg("backpressure_ack", "Processing your request\u2026")
        await send_backpressure(text)


def parse_reply_to_id(reply_to_id: str | None) -> int | None:
    """Parse a string reply_to_id into an int, returning None on bad input."""
    if reply_to_id is None:
        return None
    try:
        return int(reply_to_id)
    except ValueError:
        log.warning(
            "parse_reply_to_id: invalid reply_to_id=%r, ignoring",
            reply_to_id,
        )
        return None
