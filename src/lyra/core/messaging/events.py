"""LLM streaming event types.

These frozen dataclasses represent the raw events emitted by LLM drivers during
streaming. They form the input side of the LLM → StreamProcessor → RenderEvent
pipeline.

They live in ``core/`` rather than ``llm/`` because the type system is a
domain-level contract consumed by both sides of the pipeline — keeping it here
lets ``llm/`` depend on ``core/`` unidirectionally (enforced by ``import-linter``).

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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from roxabi_contracts.errors import WorkerError


@dataclass(frozen=True)
class TextLlmEvent:
    """A chunk of text from the LLM response stream."""

    text: str


@dataclass(frozen=True)
class ThinkingLlmEvent:
    """A chunk of extended-thinking text from the LLM (Slice 4 of #1096).

    Emitted by cli_streaming_parser on Anthropic CLI thinking_delta
    events (content_block.type == "thinking" + delta.type ==
    "thinking_delta" under --effort low|medium|high|xhigh|max).
    """

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
class ToolUseDeltaLlmEvent:
    """Tool-use input streaming chunk (partial JSON fragment).

    Slice 3 of #1096. Emitted on Anthropic CLI ``content_block_delta`` events
    whose ``delta.type == "input_json_delta"`` (per ADR-028
    ``--include-partial-messages``). ``partial_json`` is only valid JSON when
    concatenated with the rest of the deltas for the same ``tool_id``.
    """

    tool_id: str
    partial_json: str


@dataclass(frozen=True)
class ToolUseEndLlmEvent:
    """Tool-use content block end (input streaming complete).

    Slice 3 of #1096. Emitted on Anthropic CLI ``content_block_stop`` events
    that close a previously-opened ``tool_use`` block.
    """

    tool_id: str


@dataclass(frozen=True)
class ToolResultLlmEvent:
    """Tool execution result, paired by ``tool_id`` with a prior ``ToolUseLlmEvent``.

    Slice 3 of #1096. Emitted on Anthropic CLI user-message blocks of
    ``type=tool_result``. ``content`` is rendered text-only this slice;
    list-of-typed-blocks are concatenated with ``[{type}]`` placeholders for
    non-text blocks (rich rendering deferred to a later slice).
    """

    tool_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class ResultLlmEvent:
    """Final event in every stream — signals turn completion.

    ``cost_usd`` is always ``None`` for ``ClaudeCliDriver`` (not present in
    NDJSON result envelope).

    ``error_text`` is the driver-curated **presentation cache** of
    ``worker_error.message`` — an in-process convenience field surfaced
    directly to user-facing renderers (e.g. ``_shared_streaming_emitter``)
    so adapters don't have to reach into the structured ``worker_error``
    envelope for the display string. Populated by drivers/parsers (see
    ``cli_streaming_parser.py``); always co-populated with ``worker_error``
    on terminal failure events. ``None`` or empty on success. Not present on
    NATS wire contracts — only ``worker_error`` crosses the wire. See
    ADR-066 archive Status for the dual-field rationale.
    """

    is_error: bool
    duration_ms: int
    cost_usd: float | None = None
    error_text: str | None = None
    session_id: str | None = None
    worker_error: "WorkerError | None" = None


# Union type exported for type annotations and ``isinstance`` checks.
LlmEvent = (
    TextLlmEvent
    | ThinkingLlmEvent
    | ToolUseLlmEvent
    | ToolUseDeltaLlmEvent
    | ToolUseEndLlmEvent
    | ToolResultLlmEvent
    | ResultLlmEvent
)

__all__ = [
    "LlmEvent",
    "ResultLlmEvent",
    "TextLlmEvent",
    "ThinkingLlmEvent",
    "ToolResultLlmEvent",
    "ToolUseDeltaLlmEvent",
    "ToolUseEndLlmEvent",
    "ToolUseLlmEvent",
]
