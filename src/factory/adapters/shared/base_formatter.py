"""BaseFormatter — abstract contract for platform outbound formatters.

Root cause (P5 audit): TelegramFormatter and DiscordFormatter declare identical
method signatures with no shared interface, causing silent drift as new methods
are added to one but not the other.

Correction class: Archi — introduces an abstract base that both formatters must
satisfy. Level L = interface/contract layer (not symptom layer = the formatters
themselves). The concrete per-platform implementations remain in
telegram_formatter.py / discord_formatter.py unchanged.

Relationship to OutboundFormatter (outbound/): OutboundFormatter is a Protocol
(structural subtyping, duck-typing) used by OutboundEmitter to call formatter
methods without an explicit import. BaseFormatter is a nominal ABC that
TelegramFormatter and DiscordFormatter explicitly inherit, giving static-analysis
tools (pyright) full visibility into the contract. Both coexist: OutboundEmitter
uses the Protocol; the formatters inherit BaseFormatter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory.core.messaging.render_events import (
        ReasoningDeltaRenderEvent,
        ReasoningEndRenderEvent,
        ReasoningStartRenderEvent,
    )

__all__ = ["BaseFormatter"]


class BaseFormatter(ABC):
    """Abstract base for platform outbound formatters.

    Formatters implement two concerns:
    1. Pure formatting: chunk, render_text, render_buttons, dim_italic.
    2. Platform I/O mechanics: send_placeholder, edit_placeholder_text,
       send_trace_placeholder, send_message, send_fallback, edit_reasoning,
       edit_tool_recap.

    Subclasses must implement all abstract methods.  Per-platform state
    (e.g. ReasoningAccumulator, last-edit timestamp) is managed by the
    subclass — this base imposes no constructor shape to preserve MRO
    flexibility (Discord multiple inheritance pattern).
    """

    # ------------------------------------------------------------------
    # Pure formatting — no I/O
    # ------------------------------------------------------------------

    @abstractmethod
    def placeholder_text(self) -> str:
        """Return the placeholder text shown while the response is being generated."""

    @abstractmethod
    def chunk(self, text: str) -> list[str]:
        """Split *text* into platform-legal chunks (respects max-length)."""

    @abstractmethod
    def render_text(self, text: str) -> list[str]:
        """Escape and split *text* into platform-legal chunks."""

    @abstractmethod
    def render_buttons(self, buttons: Any) -> Any:
        """Convert generic button spec to platform-native button/view object."""

    @abstractmethod
    def dim_italic(self, text: str) -> str:
        """Wrap *text* in dim-italic markup for the platform."""

    @abstractmethod
    def get_msg(self, key: str, fallback: str) -> str:
        """Return a localised message string via the adapter's message manager."""

    # ------------------------------------------------------------------
    # Platform I/O mechanics
    # ------------------------------------------------------------------

    @abstractmethod
    async def send_placeholder(self) -> tuple[Any, int]:
        """Send the initial placeholder message.

        Returns (message_object, message_id).  The message object is
        platform-specific and opaque to callers — it is passed back into
        edit_placeholder_text / edit_reasoning / edit_tool_recap.
        """

    @abstractmethod
    async def edit_placeholder_text(self, ph: Any, text: str) -> None:
        """Edit the placeholder message *ph* to display *text*.

        *ph* is the first element of the tuple returned by send_placeholder.
        Silently skips on platform-level errors (edit-conflict, rate-limit).
        """

    @abstractmethod
    async def send_trace_placeholder(self) -> tuple[Any, int | None]:
        """Send the trace-indicator placeholder ("🔧 …").

        Returns (message_object, message_id).  message_id may be None if the
        platform send failed — callers must guard against None trace_obj before
        calling edit_reasoning / edit_tool_recap.
        """

    @abstractmethod
    async def send_message(self, text: str) -> int | None:
        """Send *text* as a final (non-streaming) message.

        Chunks the text if necessary.  Returns the message_id of the last
        sent chunk, or None if all sends failed.
        """

    @abstractmethod
    async def send_fallback(self, text: str) -> int | None:
        """Send *text* as a fallback when normal delivery fails.

        Implementations may re-use platform-specific send paths.
        Returns message_id or None.
        """

    @abstractmethod
    async def edit_reasoning(
        self,
        trace_obj: Any,
        event: (
            "ReasoningStartRenderEvent"
            " | ReasoningDeltaRenderEvent"
            " | ReasoningEndRenderEvent"
        ),
    ) -> None:
        """Render a reasoning event into the trace placeholder.

        *trace_obj* is the first element of the tuple returned by
        send_trace_placeholder.  When trace_obj is None (placeholder send
        failed) implementations must bail silently.

        Throttling of delta edits is managed by the ReasoningAccumulator held
        by the subclass.
        """

    @abstractmethod
    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None:
        """Render tool-recap card *lines* into the trace placeholder.

        *done* signals whether the tool call sequence is complete (may affect
        styling, e.g. green vs blue embed on Discord).  Silently skips on
        platform-level errors.
        """
