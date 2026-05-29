"""OutboundFormatter — per-platform formatting stage.

Composes into OutboundEmitter alongside ThrottleCapability and OutboundErrorHandler.
Per-platform impls live in adapters/{telegram,discord}/<platform>_formatter.py and
encapsulate chunking, escaping, button rendering, and trace rendering for reasoning
and tool-recap callbacks.

The Protocol's edit_reasoning and edit_tool_recap have no-op default callables
exported from this module so adapters that don't opt in render nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from lyra.core.messaging.render_events import (
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
    async def edit_placeholder_text(self, ph: Any, text: str) -> None: ...
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

    async def edit_placeholder_text(self, ph: Any, text: str) -> None:
        pass

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
