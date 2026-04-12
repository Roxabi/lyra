"""LLM streaming event types.

These frozen dataclasses represent the raw events emitted by LLM drivers during
streaming. They form the input side of the LLM → StreamProcessor → RenderEvent
pipeline.

No framework imports (aiogram, discord, anthropic) are permitted in this module.

Immutability contract
---------------------
All classes use ``frozen=True`` which prevents *re-assignment* of fields
(``event.field = x`` raises ``FrozenInstanceError``) but does **not** prevent
in-place mutation of mutable containers (``event.input["k"] = v`` succeeds).
Callers must never mutate event objects after construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TextLlmEvent:
    """A chunk of text from the LLM response stream."""

    text: str


@dataclass(frozen=True)
class ToolUseLlmEvent:
    """Emitted when the LLM calls a tool.

    ``input`` is empty at ``ContentBlockStart`` time (SDK); the full input dict
    is populated via ``InputJsonDelta`` events but V1 only tracks tool name/id
    for real-time visibility.
    """

    tool_name: str
    tool_id: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultLlmEvent:
    """Final event in every stream — signals turn completion.

    ``cost_usd`` is always ``None`` for ``ClaudeCliDriver`` (not present in
    NDJSON result envelope).

    ``error_text`` carries the backend-reported error message when
    ``is_error=True`` (e.g. ``"Not logged in · Please run /login"`` from the
    CLI's ``result`` field). Consumers surface it to the user when no other
    text was streamed; ``None`` or empty on success.
    """

    is_error: bool
    duration_ms: int
    cost_usd: float | None = None
    error_text: str | None = None


# Union type exported for type annotations and ``isinstance`` checks.
LlmEvent = TextLlmEvent | ToolUseLlmEvent | ResultLlmEvent

__all__ = [
    "LlmEvent",
    "ResultLlmEvent",
    "TextLlmEvent",
    "ToolUseLlmEvent",
]
