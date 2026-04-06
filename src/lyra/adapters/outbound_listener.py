"""Structural protocol for adapter-side outbound listeners.

Defines the minimum surface TelegramAdapter / DiscordAdapter call on their
cached listener. NatsOutboundListener satisfies this protocol structurally
(no inheritance required). Keeps adapter code transport-agnostic at the
type level.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage


class OutboundListener(Protocol):
    """Surface that adapter code requires from its outbound listener.

    Matches the shape of NatsOutboundListener. Not @runtime_checkable —
    this mirrors the ChannelAdapter protocol convention in
    core/hub/hub_protocol.py.
    """

    def cache_inbound(self, msg: InboundMessage) -> None:
        """Store msg so outbound correlation can retrieve it by stream_id."""
        ...

    async def start(self) -> None:
        """Start consuming outbound events from the transport."""
        ...

    async def stop(self) -> None:
        """Stop consuming and release transport resources."""
        ...
