"""WireParser Protocol — platform-agnostic raw-event-to-InboundMessage contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.inbound.context import InboundContext


class WireParser(Protocol):
    """Structural protocol for platform-specific wire parsers.

    Implementors translate a raw platform event (aiogram ``Message``,
    discord.py ``Message``, etc.) into an ``InboundMessage``.  Return
    ``None`` to signal that the raw event should be silently discarded
    before reaching the router (e.g. the event is for a different bot).
    """

    def parse(self, raw: Any, ctx: InboundContext) -> InboundMessage | None:
        """Parse *raw* into an ``InboundMessage``, or ``None`` to drop."""
        ...
