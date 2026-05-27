"""Abstract base class for Lyra outbound adapters.

Defines the shared contract for Telegram and Discord outbound adapters:
- abstract send() — platform-specific complete reply
- concrete send_streaming() — shared algorithm via OutboundEmitter
- abstract _make_emitter() — platform-specific stage-composed emitter factory
- abstract _make_streaming_callbacks() — legacy callback factory (kept during
  S4→S7 transition; consumed by some tests directly)
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

from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.outbound.emitter import OutboundEmitter, PlatformCallbacks

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage, OutboundMessage
    from lyra.core.messaging.render_events import RenderEvent

__all__ = ["OutboundAdapterBase"]


class OutboundAdapterBase(ABC):
    """Shared contract for Lyra outbound channel adapters.

    Subclasses must implement:
    - send()
    - _make_emitter() — stage-composed factory (#1279 T15/T19)
    - _make_streaming_callbacks() — legacy callbacks factory (kept until S7 cleanup)
    - _start_typing()
    - _cancel_typing()

    send_streaming() is provided as a concrete method and must NOT be overridden.
    It now delegates to _make_emitter so each platform's stage composition
    (formatter + typing + error_handler) is the active path.

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
        """Stream reply using the shared OutboundEmitter algorithm."""
        emitter = self._make_emitter(original_msg, outbound)
        # Single WRITE site for tool_display_config — see ADR-073.
        emitter.tool_display_config = (
            getattr(self, "_tool_display_config", None) or ToolDisplayConfig()
        )
        await emitter.run(events)

    @abstractmethod
    def _make_emitter(
        self,
        original_msg: "InboundMessage",
        outbound: "OutboundMessage | None",
    ) -> OutboundEmitter:
        """Build the platform-specific OutboundEmitter (stage-composed).

        Composes OutboundFormatter + ThrottleCapability + OutboundErrorHandler
        per #1279 Phase 2. Subclasses construct the per-platform formatter
        + typing indicator + error handler and return OutboundEmitter wired
        through the existing PlatformCallbacks dataclass (transitional; the
        dataclass itself is removed in S7 along with _make_streaming_callbacks).
        """

    @abstractmethod
    def _make_streaming_callbacks(
        self,
        original_msg: "InboundMessage",
        outbound: "OutboundMessage | None",
    ) -> PlatformCallbacks:
        """Legacy callbacks factory — consumed by `_make_emitter` and a few tests.

        Removed in S7 of #1279 once the formatter Protocol fully owns the
        callback surface.
        """

    @abstractmethod
    def _start_typing(self, scope_id: int) -> None:
        """Start the typing indicator for scope_id."""

    @abstractmethod
    def _cancel_typing(self, scope_id: int) -> None:
        """Cancel the typing indicator for scope_id."""
