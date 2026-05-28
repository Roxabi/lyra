"""Render event types for the streaming pipeline.

These frozen dataclasses represent the output side of the StreamProcessor —
events consumed by outbound adapters (Telegram, Discord, TTS tee, turn logger).

No framework imports (aiogram, discord, anthropic) are permitted in this module.

Immutability contract
---------------------
All classes use ``frozen=True`` which prevents *re-assignment* of fields
(``event.field = x`` raises ``FrozenInstanceError``) but does **not** prevent
in-place mutation of mutable containers (e.g. ``event.edits.append(...)``
succeeds). ``StreamProcessor`` must always construct a fresh copy of any
mutable accumulator before passing it into an event — never share the live
accumulator reference with an already-emitted event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION_TEXT_START_RENDER_EVENT = 1
SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT = 1
SCHEMA_VERSION_TEXT_END_RENDER_EVENT = 1
SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT = 1
SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT = 1
SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT = 1
SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT = 1
SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT = 1
SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT = 1
SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT = 1
SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT = 1
SCHEMA_VERSION_REASONING_START_RENDER_EVENT = 1
SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT = 1
SCHEMA_VERSION_REASONING_END_RENDER_EVENT = 1


@dataclass(frozen=True)
class SilentCounts:
    """Counts of tool calls that are filtered from the visible summary.

    Read, Grep, and Glob operations are always silent — they increment these
    counters rather than appearing in the tool summary card.
    """

    reads: int = 0
    greps: int = 0
    globs: int = 0


@dataclass(frozen=True)
class FileEditSummary:
    """Per-file edit summary accumulated by ``StreamProcessor``.

    When ``count > names_threshold``, ``edits`` is cleared and only ``count``
    is shown (count mode). When ``len(files) >= group_threshold``, the adapter
    renders a grouped summary instead of per-file detail.

    Use :meth:`snapshot` to produce a safe copy before embedding in an emitted
    event — never pass a live accumulator reference directly.
    """

    path: str
    edits: list[str] = field(default_factory=list)
    count: int = 0

    def snapshot(self) -> "FileEditSummary":
        """Return a shallow copy safe to embed in an emitted ``RenderEvent``."""
        return FileEditSummary(path=self.path, edits=list(self.edits), count=self.count)


@dataclass(frozen=True)
class TextStartRenderEvent:
    """Marks the beginning of an assistant text block.

    Slice 2 of #1096 (#1099). One emitted per block; bracketed by
    ``TextEndRenderEvent``. ``message_id`` is per-block (not per-turn) —
    supports interleaved text→tool→text.
    """

    message_id: str
    role: Literal["assistant"] = "assistant"
    schema_version: int = SCHEMA_VERSION_TEXT_START_RENDER_EVENT


@dataclass(frozen=True)
class TextDeltaRenderEvent:
    """One streaming chunk inside an open text block.

    Slice 2 of #1096 (#1099). ``message_id`` matches the bracketing
    ``TextStartRenderEvent`` / ``TextEndRenderEvent``.
    """

    message_id: str
    delta: str
    schema_version: int = SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT


@dataclass(frozen=True)
class TextEndRenderEvent:
    """Marks the end of an assistant text block.

    Slice 2 of #1096 (#1099). Emitted at 4 boundaries: ToolUse start,
    ``ResultLlmEvent``, truncation (no Result received), exception path.
    """

    message_id: str
    schema_version: int = SCHEMA_VERSION_TEXT_END_RENDER_EVENT


@dataclass(frozen=True)
class TextChunkRenderEvent:
    """EXPERIMENTAL — convenience event for adapters that don't need granularity.

    Slice 2 of #1096 (#1099). Currently DEFINED BUT NOT EMITTED by
    ``StreamProcessor``. Shape is revisable until the first emitter wires up
    (planned post-Slice-5 once a concrete consumer — TTS tee, AG-UI bridge,
    or rich-rendering adapter — needs it).
    """

    message_id: str
    delta: str
    role: Literal["assistant"] = "assistant"
    schema_version: int = SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT


@dataclass(frozen=True)
class RunStartedRenderEvent:
    """Run lifecycle: stream begin. ``run_id`` mirrors the per-turn ``trace_id``.

    Slice 1 of #1096 — additive surface. Adapters initially ignore (no UX).
    Future slices may render a banner or expose the run_id in observability.
    """

    run_id: str
    schema_version: int = SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT


@dataclass(frozen=True)
class RunFinishedRenderEvent:
    """Run lifecycle: clean stream end (no exception).

    ``outcome=success`` is the normal terminal state. ``outcome=interrupt``
    is reserved for future cancellation paths and is not emitted in Slice 1.
    """

    run_id: str
    outcome: Literal["success", "interrupt"] = "success"
    schema_version: int = SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT


@dataclass(frozen=True)
class RunErrorRenderEvent:
    """Run lifecycle: error terminal — infrastructure exception OR soft error.

    Two paths emit this event:

    - **Infrastructure exception** (``StreamProcessor`` ``try``/``except``):
      ``message=type(exc).__name__`` — never ``str(exc)`` (exception strings
      can carry hostnames, file paths, auth tokens from httpx/aiohttp/NATS
      errors; this event is published on the NATS bus where any subscriber
      can read it).
    - **Soft error** (``ResultLlmEvent.is_error=True``): the LLM backend
      returned an error response. ``message`` carries ``ResultLlmEvent.
      error_text`` — driver-curated user-facing text (e.g. "Not logged in ·
      Please run /login"), safe to forward on the bus.

    In both cases, the adapter dispatch ladder flags the turn as error so the
    final rendered message gets an ``❌`` prefix.

    ``code`` is a key from the canonical ``roxabi_contracts.errors.KNOWN_CODES``
    registry (e.g. ``stream.error`` for an infrastructure exception, or a
    ``WorkerError`` code such as ``cli.auth`` / ``llm.rate_limit`` for soft
    errors). It is populated from the originating ``SanitizedError.code`` at
    both emit sites. ``None`` is retained only as the historical / pre-taxonomy
    sentinel for events serialized before #1113.
    """

    run_id: str
    message: str
    code: str | None = None
    schema_version: int = SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT


@dataclass(frozen=True)
class ToolCallStartRenderEvent:
    """Tool-call lifecycle: a single tool invocation begins.

    Slice 3 of #1096. Streamed alternative to the post-hoc
    Per-tool correlator. ``tool_call_id`` is the cross-event
    correlator — a verbatim pass-through of the CLI's ``content_block.id`` for
    the corresponding ``tool_use`` block.
    """

    tool_call_id: str
    tool_name: str
    input: dict[str, Any] | None = None
    schema_version: int = SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT


@dataclass(frozen=True)
class ToolCallArgsRenderEvent:
    """Tool-call lifecycle: a chunk of streamed tool input arguments.

    ``delta`` is a *partial JSON fragment* (e.g. ``'{"foo": '``) — it is only
    valid JSON when concatenated with the rest of the deltas for the same
    ``tool_call_id``. Adapters that need parsed args wait for ``ToolCallEnd``
    and reconstruct from accumulated deltas.
    """

    tool_call_id: str
    delta: str
    schema_version: int = SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT


@dataclass(frozen=True)
class ToolCallEndRenderEvent:
    """Tool-call lifecycle: tool input streaming complete (no more args).

    Emitted on the CLI's ``content_block_stop`` for the corresponding
    ``tool_use`` block, OR synthesized by ``StreamProcessor`` at end-of-turn
    for any ``tool_call_id`` that saw a ``Start`` but no matching ``stop``
    (orphan recovery; logged at WARN level).
    """

    tool_call_id: str
    schema_version: int = SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT


@dataclass(frozen=True)
class ToolCallResultRenderEvent:
    """Tool-call lifecycle: the executed tool returned a result.

    Emitted from the CLI's user-message ``tool_result`` block. ``content`` is
    rendered text-only this slice — list-of-typed-blocks are concatenated with
    placeholders for non-text content (rich rendering deferred).
    """

    tool_call_id: str
    content: str
    is_error: bool = False
    schema_version: int = SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT


@dataclass(frozen=True)
class ReasoningStartRenderEvent:
    """First Reasoning event in a thinking block (Slice 4 of #1096)."""

    message_id: str
    schema_version: int = SCHEMA_VERSION_REASONING_START_RENDER_EVENT


@dataclass(frozen=True)
class ReasoningDeltaRenderEvent:
    """One chunk of reasoning text (Slice 4 of #1096)."""

    message_id: str
    delta: str
    schema_version: int = SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT


@dataclass(frozen=True)
class ReasoningEndRenderEvent:
    """Final Reasoning event closing a thinking block (Slice 4 of #1096)."""

    message_id: str
    schema_version: int = SCHEMA_VERSION_REASONING_END_RENDER_EVENT


# Union type exported for type annotations and ``isinstance`` checks.
RenderEvent = (
    TextStartRenderEvent
    | TextDeltaRenderEvent
    | TextEndRenderEvent
    | TextChunkRenderEvent
    | RunStartedRenderEvent
    | RunFinishedRenderEvent
    | RunErrorRenderEvent
    | ToolCallStartRenderEvent
    | ToolCallArgsRenderEvent
    | ToolCallEndRenderEvent
    | ToolCallResultRenderEvent
    | ReasoningStartRenderEvent
    | ReasoningDeltaRenderEvent
    | ReasoningEndRenderEvent
)

__all__ = [
    "FileEditSummary",
    "ReasoningDeltaRenderEvent",
    "ReasoningEndRenderEvent",
    "ReasoningStartRenderEvent",
    "RenderEvent",
    "RunErrorRenderEvent",
    "RunFinishedRenderEvent",
    "RunStartedRenderEvent",
    "SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT",
    "SCHEMA_VERSION_REASONING_END_RENDER_EVENT",
    "SCHEMA_VERSION_REASONING_START_RENDER_EVENT",
    "SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT",
    "SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT",
    "SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT",
    "SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT",
    "SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT",
    "SCHEMA_VERSION_TEXT_END_RENDER_EVENT",
    "SCHEMA_VERSION_TEXT_START_RENDER_EVENT",
    "SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT",
    "SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT",
    "SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT",
    "SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT",
    "SilentCounts",
    "TextChunkRenderEvent",
    "TextDeltaRenderEvent",
    "TextEndRenderEvent",
    "TextStartRenderEvent",
    "ToolCallArgsRenderEvent",
    "ToolCallEndRenderEvent",
    "ToolCallResultRenderEvent",
    "ToolCallStartRenderEvent",
]
