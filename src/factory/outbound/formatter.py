"""OutboundFormatter — per-platform formatting stage.

Composes into OutboundEmitter alongside ThrottleCapability and OutboundErrorHandler.
Per-platform impls live in adapters/{telegram,discord}/<platform>_formatter.py and
encapsulate chunking, escaping, button rendering, and trace rendering for reasoning
and tool-recap callbacks.

The Protocol's edit_reasoning and edit_tool_recap have no-op default callables
exported from this module so adapters that don't opt in render nothing.

``BaseFormatter`` (ABC) lives here as the nominal inheritance contract for
``TelegramFormatter`` / ``DiscordFormatter``.  It mirrors the ``OutboundFormatter``
Protocol shape so static-analysis tools (pyright) get full nominal visibility into
the contract while ``OutboundEmitter`` continues to use the structural Protocol.
ADR-073 §Decision: stage primitives live in their stage module, not in per-adapter
dirs or adapter-shared dirs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from factory.core.messaging.render_events import (
        ReasoningDeltaRenderEvent,
        ReasoningEndRenderEvent,
        ReasoningStartRenderEvent,
    )


async def default_no_op_edit_reasoning(
    trace_obj: Any,
    event: "ReasoningStartRenderEvent | ReasoningDeltaRenderEvent | ReasoningEndRenderEvent",  # noqa: E501
) -> None:
    """Default no-op — adapters that haven't opted in render nothing."""
    del trace_obj, event


async def default_no_op_edit_tool_recap(
    trace_obj: Any,
    lines: list[str],
    done: bool,
) -> None:
    """Default no-op — adapters that haven't opted in render nothing."""
    del trace_obj, lines, done


class OutboundFormatter(Protocol):
    """Per-platform formatting stage for OutboundEmitter.

    This Protocol deliberately spans two responsibility axes (S7a decision, #1508):

    **(a) Pure formatting** — no platform I/O, fully synchronous:
        ``chunk``, ``render_text``, ``render_buttons``, ``dim_italic``,
        ``placeholder_text``

    **(b) Platform-I/O mechanics** — talk to the platform SDK (all async):
        ``get_msg``, ``send_placeholder``, ``edit_placeholder_text``,
        ``send_trace_placeholder``, ``send_message``, ``send_fallback``,
        ``edit_reasoning``, ``edit_tool_recap``

    **Why one Protocol?** S7a explicitly chose Option 1 (extend the formatter
    to own both axes) over Option 2 (a separate ``OutboundSender`` Protocol)
    to keep ``_make_emitter`` injection arity low — a single object is injected
    instead of two, and the emitter has one call-site surface.  The SRP cost is
    accepted at N=2 platforms (Telegram + Discord).

    **Re-evaluate at N≥3.** If a third platform adapter lands, the arity
    argument weakens (one extra param is cheap) while the SRP win grows
    (formatting logic is genuinely platform-agnostic).  At that point, extract
    an ``OutboundSender`` Protocol and split the implementations accordingly.

    **Guard for new methods:** every addition to this Protocol must be
    consciously placed in axis (a) or (b).  A method that touches the platform
    SDK belongs in (b); one that only transforms text/data belongs in (a).
    Mixing the two inside a single method is the boundary to avoid.
    """

    def placeholder_text(self) -> str: ...
    def chunk(self, text: str) -> list[str]: ...
    def render_text(self, text: str) -> list[str]: ...
    def render_buttons(self, buttons: Any) -> Any: ...
    def dim_italic(self, text: str) -> str: ...

    def get_msg(self, key: str, fallback: str) -> str: ...

    async def send_placeholder(self) -> tuple[Any, int | None]: ...
    async def edit_placeholder_text(
        self, ph: Any, text: str, *, finalize: bool = False
    ) -> None: ...
    async def send_trace_placeholder(self) -> tuple[Any, int | None]: ...
    async def send_message(self, text: str) -> int | None: ...
    async def send_fallback(self, text: str) -> int | None: ...

    async def edit_reasoning(
        self,
        trace_obj: Any,
        event: "ReasoningStartRenderEvent | ReasoningDeltaRenderEvent | ReasoningEndRenderEvent",  # noqa: E501
    ) -> None: ...

    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None: ...


class BadFormatter:
    """Null-object OutboundFormatter for invalid inbound messages.

    Returned by adapter ``_make_emitter`` when ``_validate_inbound`` fails:
    ``send_placeholder`` / ``send_trace_placeholder`` raise so
    ``OutboundErrorHandler.guard`` routes to the fallback path; every other
    method is a safe no-op. Single stage-level null object — replaces the
    per-platform ``_BadTelegramFormatter`` / ``_BadDiscordFormatter`` twins
    (target-axis-trap, #1501 review).
    """

    def __init__(self, error_msg: str) -> None:
        self._error_msg = error_msg

    def placeholder_text(self) -> str:
        return "…"

    def chunk(self, text: str) -> list[str]:
        return [text]

    def render_text(self, text: str) -> list[str]:
        return [text]

    def render_buttons(self, buttons: Any) -> Any:
        return buttons

    def dim_italic(self, text: str) -> str:
        return text

    def get_msg(self, key: str, fallback: str) -> str:
        return fallback

    async def send_placeholder(self) -> tuple[Any, int | None]:
        raise ValueError(self._error_msg)

    async def edit_placeholder_text(
        self, ph: Any, text: str, *, finalize: bool = False
    ) -> None:
        del ph, text, finalize

    async def send_trace_placeholder(self) -> tuple[Any, int | None]:
        raise ValueError(self._error_msg)

    async def send_message(self, text: str) -> int | None:
        return None

    async def send_fallback(self, text: str) -> int | None:
        return None

    async def edit_reasoning(self, trace_obj: Any, event: Any) -> None:
        pass

    async def edit_tool_recap(
        self, trace_obj: Any, lines: list[str], done: bool
    ) -> None:
        pass


class BaseFormatter(ABC):
    """Abstract base for platform outbound formatters (nominal ABC contract).

    Mirrors ``OutboundFormatter`` Protocol shape so static-analysis tools
    (pyright) have full nominal visibility into the contract.  Placed here
    (stage module) per ADR-073 §Decision: stage primitives live in their
    stage module, not in per-adapter dirs or adapter-shared dirs.

    ``OutboundEmitter`` uses the structural ``OutboundFormatter`` Protocol;
    ``TelegramFormatter`` / ``DiscordFormatter`` inherit this ABC so that
    any missing method raises ``TypeError`` at class-definition time.

    Per-platform state (e.g. ``ReasoningAccumulator``, last-edit timestamp)
    is managed by the subclass — this base imposes no constructor shape to
    preserve MRO flexibility (Discord multiple-inheritance pattern).
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
        """Send the initial placeholder; returns (message_object, message_id)."""

    @abstractmethod
    async def edit_placeholder_text(
        self, ph: Any, text: str, *, finalize: bool = False
    ) -> None:
        """Edit the placeholder message *ph* to display *text*."""
        del finalize

    @abstractmethod
    async def send_trace_placeholder(self) -> tuple[Any, int | None]:
        """Send the trace-indicator placeholder; returns (message_object, message_id)."""  # noqa: E501

    @abstractmethod
    async def send_message(self, text: str) -> int | None:
        """Send *text* as a final (non-streaming) message; returns message_id or None."""  # noqa: E501

    @abstractmethod
    async def send_fallback(self, text: str) -> int | None:
        """Send *text* as fallback when normal delivery fails; returns message_id or None."""  # noqa: E501

    @abstractmethod
    async def edit_reasoning(
        self,
        trace_obj: Any,
        event: "ReasoningStartRenderEvent | ReasoningDeltaRenderEvent | ReasoningEndRenderEvent",  # noqa: E501
    ) -> None:
        """Render a reasoning event into the trace placeholder."""

    @abstractmethod
    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None:
        """Render tool-recap card *lines* into the trace placeholder."""
