"""Channel-agnostic StreamProcessor: LlmEvent → RenderEvent pipeline.

Consumes an async stream of ``LlmEvent`` objects (from any LLM driver) and
produces ``RenderEvent`` objects consumed by outbound adapters (Telegram,
Discord, TTS tee, turn logger).

Pipeline contract
-----------------
- ``TextLlmEvent``    → accumulate text; hold until ``ResultLlmEvent``
- ``ToolUseLlmEvent`` → accumulate into per-tool buckets; emit throttled
                         ``ToolSummaryRenderEvent`` mid-turn
- ``ResultLlmEvent``  → unconditionally emit final ``ToolSummaryRenderEvent``
                         (if any tool events occurred), then emit ``TextRenderEvent``

Hexagonal boundary
------------------
No imports from ``aiogram``, ``discord``, or ``anthropic`` are permitted here.
Only stdlib and lyra-internal modules may be used.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from lyra.core.render_events import (
    FileEditSummary,
    RenderEvent,
    SilentCounts,
    TextRenderEvent,
    ToolSummaryRenderEvent,
)
from lyra.core.tool_display_config import ToolDisplayConfig
from lyra.llm.events import LlmEvent, ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent


class StreamProcessor:
    """Translate an ``AsyncIterator[LlmEvent]`` into ``AsyncIterator[RenderEvent]``.

    One instance per turn — do not reuse across turns.

    Parameters
    ----------
    config:
        Controls display thresholds, bash truncation, throttle window, and
        which tool names surface in the summary card.
    """

    def __init__(self, config: ToolDisplayConfig) -> None:
        self._config = config

        # --- per-file accumulator ---
        self._files: dict[str, FileEditSummary] = {}

        # --- list accumulators ---
        self._bash: list[str] = []
        self._web_fetches: list[str] = []
        self._agent_calls: list[str] = []

        # --- silent counters ---
        self._silent_reads: int = 0
        self._silent_greps: int = 0
        self._silent_globs: int = 0

        # --- throttle state ---
        self._last_tool_emit: float | None = None

        # --- pending text ---
        self._pending_text: str = ""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def process(
        self, events: AsyncIterator[LlmEvent]
    ) -> AsyncIterator[RenderEvent]:
        """Process an async stream of ``LlmEvent`` objects.

        Yields ``RenderEvent`` objects as they are produced.

        Parameters
        ----------
        events:
            Async iterator of ``LlmEvent`` objects from any LLM driver.

        Yields
        ------
        RenderEvent
            ``ToolSummaryRenderEvent`` mid-turn (throttled) and at turn end
            (unconditional), followed by ``TextRenderEvent`` at turn end.
        """
        async for event in events:
            if isinstance(event, TextLlmEvent):
                self._pending_text += event.text

            elif isinstance(event, ToolUseLlmEvent):
                self._accumulate(event)
                if self._should_emit():
                    yield self._snapshot()

            elif isinstance(event, ResultLlmEvent):
                if self._has_any_tool_events():
                    yield self._snapshot(is_complete=True)
                yield TextRenderEvent(text=self._pending_text, is_final=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _accumulate_file_edit(self, event: ToolUseLlmEvent) -> None:
        """Update the per-file accumulator for an edit or write tool call."""
        path = event.input.get("path", event.tool_id)
        existing = self._files.get(path)
        if existing is None:
            new_count = 1
            new_edits: list[str] = [event.tool_name]
        else:
            new_count = existing.count + 1
            if new_count > self._config.names_threshold:
                # count mode — clear edits list
                new_edits = []
            else:
                # names mode — append tool name
                new_edits = list(existing.edits) + [event.tool_name]
        self._files[path] = FileEditSummary(path=path, edits=new_edits, count=new_count)

    def _accumulate(self, event: ToolUseLlmEvent) -> None:
        """Route a tool-use event into the appropriate accumulator bucket."""
        tool_key = event.tool_name.lower()

        if tool_key in ("edit", "write"):
            self._accumulate_file_edit(event)

        elif tool_key == "bash":
            command = event.input.get("command", "")
            self._bash.append(command[: self._config.bash_max_len])

        elif tool_key == "read":
            self._silent_reads += 1

        elif tool_key == "grep":
            self._silent_greps += 1

        elif tool_key == "glob":
            self._silent_globs += 1

        elif tool_key in ("web_fetch", "web_search", "webfetch", "websearch"):
            if self._config.show.get("web_fetch", False):
                self._web_fetches.append(event.input.get("url", ""))

        elif tool_key == "agent":
            if self._config.show.get("agent", False):
                self._agent_calls.append(event.input.get("description", "agent"))

        # anything else with show.get(key, False) == False → ignored

    def _should_emit(self) -> bool:
        """Return True when the throttle window has elapsed (or never fired)."""
        if self._last_tool_emit is None:
            return True
        elapsed = time.monotonic() - self._last_tool_emit
        return elapsed >= self._config.throttle_ms / 1000

    def _snapshot(self, *, is_complete: bool = False) -> ToolSummaryRenderEvent:
        """Build a ``ToolSummaryRenderEvent`` from a safe copy of all accumulators.

        Side-effect: updates ``_last_tool_emit`` to ``time.monotonic()``.
        """
        files_copy = {path: entry.snapshot() for path, entry in self._files.items()}
        event = ToolSummaryRenderEvent(
            files=files_copy,
            bash_commands=list(self._bash),
            web_fetches=list(self._web_fetches),
            agent_calls=list(self._agent_calls),
            silent_counts=SilentCounts(
                reads=self._silent_reads,
                greps=self._silent_greps,
                globs=self._silent_globs,
            ),
            is_complete=is_complete,
        )
        self._last_tool_emit = time.monotonic()
        return event

    def _has_any_tool_events(self) -> bool:
        """Return True when at least one tool accumulator is non-empty."""
        return bool(
            self._files
            or self._bash
            or self._web_fetches
            or self._agent_calls
            or self._silent_reads > 0
            or self._silent_greps > 0
            or self._silent_globs > 0
        )


__all__ = ["StreamProcessor"]
