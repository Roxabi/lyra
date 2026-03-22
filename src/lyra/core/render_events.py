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
    """

    path: str
    edits: list[str] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class TextRenderEvent:
    """Accumulated LLM text, emitted once at the end of a turn.

    In V1 text is NOT streamed incrementally — the full response accumulates
    in ``StreamProcessor`` and emits as a single event with ``is_final=True``
    after ``ResultLlmEvent`` arrives.
    """

    text: str
    is_final: bool = False


@dataclass(frozen=True)
class ToolSummaryRenderEvent:
    """Snapshot of tool activity, emitted after each tool call (throttled).

    Outbound adapters render this as a tool-activity card (Telegram edit,
    Discord embed update). The final snapshot has ``is_complete=True`` and is
    emitted unconditionally on ``ResultLlmEvent``, bypassing the throttle.

    Text-only turns (no tool calls) never emit this event — ``StreamProcessor``
    skips it when all accumulators are empty.
    """

    files: dict[str, FileEditSummary] = field(default_factory=dict)
    bash_commands: list[str] = field(default_factory=list)
    web_fetches: list[str] = field(default_factory=list)
    agent_calls: list[str] = field(default_factory=list)
    silent_counts: SilentCounts = field(default_factory=SilentCounts)
    is_complete: bool = False


# Union type exported for type annotations and ``isinstance`` checks.
RenderEvent = TextRenderEvent | ToolSummaryRenderEvent

__all__ = [
    "FileEditSummary",
    "RenderEvent",
    "SilentCounts",
    "TextRenderEvent",
    "ToolSummaryRenderEvent",
]
