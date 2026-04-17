"""Abstract base class for Lyra outbound adapters.

Defines the shared contract for Telegram and Discord outbound adapters:
- abstract send() — platform-specific complete reply
- concrete send_streaming() — shared algorithm via StreamingSession
- abstract _make_streaming_callbacks() — platform-specific callback factory
- abstract _start_typing() / _cancel_typing() — typing indicator lifecycle

Discord MRO constraint: __init__ must be a no-op. discord.Client's __init__
takes keyword args (intents=, ...) and is called via super().__init__(intents=intents)
in DiscordAdapter. OutboundAdapterBase must not call super().__init__() with any
arguments, and must not define __init__ at all (so cooperative chain flows through
discord.Client correctly).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from lyra.adapters._shared_streaming import PlatformCallbacks, StreamingSession

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage, OutboundMessage
    from lyra.core.render_events import RenderEvent

__all__ = ["OutboundAdapterBase"]


class OutboundAdapterBase(ABC):
    """Shared contract for Lyra outbound channel adapters.

    Subclasses must implement:
    - send()
    - _make_streaming_callbacks()
    - _start_typing()
    - _cancel_typing()

    send_streaming() is provided as a concrete method and must NOT be overridden.

    MRO note: __init__ is intentionally absent. DiscordAdapter uses multiple
    inheritance (discord.Client first), and discord.Client.__init__ must receive
    control without interference from this base.
    """

    @abstractmethod
    async def send(
        self,
        original_msg: "InboundMessage",
        outbound: "OutboundMessage",
    ) -> None:
        """Send a complete reply to the platform."""

    async def send_streaming(
        self,
        original_msg: "InboundMessage",
        events: "AsyncIterator[RenderEvent]",
        outbound: "OutboundMessage | None" = None,
    ) -> None:
        """Stream reply using the shared StreamingSession algorithm."""
        session = StreamingSession(
            self._make_streaming_callbacks(original_msg, outbound),
            outbound,
        )
        await session.run(events)

    @abstractmethod
    def _make_streaming_callbacks(
        self,
        original_msg: "InboundMessage",
        outbound: "OutboundMessage | None",
    ) -> PlatformCallbacks:
        """Build the platform-specific PlatformCallbacks for StreamingSession."""

    @abstractmethod
    def _start_typing(self, scope_id: int) -> None:
        """Start the typing indicator for scope_id."""

    @abstractmethod
    def _cancel_typing(self, scope_id: int) -> None:
        """Cancel the typing indicator for scope_id."""
