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
    """Per-platform formatting stage for OutboundEmitter."""

    def placeholder_text(self) -> str: ...
    def chunk(self, text: str) -> list[str]: ...
    def render_text(self, text: str) -> list[str]: ...
    def render_buttons(self, buttons: Any) -> Any: ...
    def dim_italic(self, text: str) -> str: ...

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
