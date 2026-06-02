"""Abstract base class for Lyra outbound adapters.

Defines the shared contract for Telegram and Discord outbound adapters:
- abstract send() — platform-specific complete reply
- concrete send_streaming() — shared algorithm via OutboundEmitter
- abstract _make_emitter() — platform-specific stage-composed emitter factory
- abstract _start_typing() / _cancel_typing() — typing indicator lifecycle

Discord MRO constraint: __init__ must be a no-op. discord.Client's __init__
takes keyword args (intents=, ...) and is called via super().__init__(intents=intents)
in DiscordAdapter. OutboundAdapterBase must not call super().__init__() with any
arguments, and must not define __init__ at all (so cooperative chain flows through
discord.Client correctly).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import uuid4

from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.core.trace import TraceContext
from lyra.outbound.emitter import OutboundEmitter
from lyra.transport.work_scope import WorkScope

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage, OutboundMessage
    from lyra.core.messaging.render_events import RenderEvent
    from lyra.transport.typing_publisher import TypingPublisher

log = logging.getLogger(__name__)

__all__ = ["OutboundAdapterBase"]


class OutboundAdapterBase(ABC):
    """Shared contract for Lyra outbound channel adapters.

    Subclasses must implement:
    - send()
    - _make_emitter() — stage-composed factory (formatter + throttle + error_handler)
    - _start_typing()
    - _cancel_typing()

    send_streaming() is provided as a concrete method and must NOT be overridden.
    It delegates to _make_emitter so each platform's stage composition
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
        # Single site propagating tool_display_config to the emitter — see ADR-073.
        emitter.tool_display_config = (
            getattr(self, "_tool_display_config", None) or ToolDisplayConfig()
        )
        # Inject typing publisher for pub/sub typing path (#1377).
        _tp = getattr(self, "_typing_publisher", None)
        emitter.typing_publisher = _tp
        if _tp is not None:
            try:
                _sid = int(original_msg.scope_id.rsplit(":", 1)[-1])
                emitter._work_scope = WorkScope(
                    platform=original_msg.platform,
                    bot_id=original_msg.bot_id,
                    scope_id=_sid,
                    trace_id=TraceContext.get_trace_id() or uuid4().hex,
                )
            except ValueError:
                log.warning(
                    "typing disabled — bad scope_id/work_scope: "
                    "scope_id=%r platform=%r bot_id=%r",
                    original_msg.scope_id,
                    original_msg.platform,
                    original_msg.bot_id,
                )
                emitter.typing_publisher = None
        await emitter.run(events)

    def configure_tool_display(self, config: ToolDisplayConfig | None) -> None:
        """Store the per-instance tool-display config (post-construction setter).

        OutboundAdapterBase has no __init__ (Discord MRO); this is the single
        permitted per-instance write point. Stores None as-is — defaulting to
        ToolDisplayConfig() happens only in send_streaming's read path.
        """
        self._tool_display_config = config

    def configure_typing_publisher(self, publisher: "TypingPublisher | None") -> None:
        """Store the per-instance typing publisher (post-construction setter).

        Mirrors configure_tool_display pattern — avoids MRO issues on Discord.
        """
        self._typing_publisher = publisher

    @abstractmethod
    def _make_emitter(
        self,
        original_msg: "InboundMessage",
        outbound: "OutboundMessage | None",
    ) -> OutboundEmitter:
        """Build the platform-specific OutboundEmitter (stage-composed).

        Composes OutboundFormatter + ThrottleCapability + OutboundErrorHandler.
        Subclasses construct the per-platform formatter + typing indicator +
        error handler and return OutboundEmitter wrapping the formatter.
        """

    @abstractmethod
    def _start_typing(self, scope_id: int) -> None:
        """Start the typing indicator for scope_id."""

    @abstractmethod
    def _cancel_typing(self, scope_id: int) -> None:
        """Cancel the typing indicator for scope_id."""
