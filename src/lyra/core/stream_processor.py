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
from collections.abc import AsyncGenerator, AsyncIterator

from lyra.core.events import LlmEvent, TextLlmEvent, ToolUseLlmEvent
from lyra.core.render_events import (
    FileEditSummary,
    RenderEvent,
    SilentCounts,
    TextRenderEvent,
    ToolSummaryRenderEvent,
)
from lyra.core.tool_display_config import ToolDisplayConfig


class StreamProcessor:
    """Translate an ``AsyncIterator[LlmEvent]`` into ``AsyncIterator[RenderEvent]``.

    One instance per turn — do not reuse across turns.

    Parameters
    ----------
    config:
        Controls display thresholds, bash truncation, throttle window, and
        which tool names surface in the summary card.
    show_intermediate:
        When ``True`` (default), text that the model emits *before* a tool
        call is flushed as ``TextRenderEvent(is_final=False)`` so adapters
        can display it progressively.  When ``False``, that pre-tool text is
        still accumulated and emitted as part of the final
        ``TextRenderEvent(is_final=True)``, matching the legacy behaviour.
    """

    def __init__(
        self, config: ToolDisplayConfig, *, show_intermediate: bool = True
    ) -> None:
        self._config = config
        self._show_intermediate = show_intermediate

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

        # --- reuse guard ---
        self._consumed: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def process(  # noqa: C901 — event-type dispatch + terminal fallbacks
        self, events: AsyncIterator[LlmEvent]
    ) -> AsyncGenerator[RenderEvent, None]:
        """Process an async stream of ``LlmEvent`` objects.

        Yields ``RenderEvent`` objects as they are produced.

        Parameters
        ----------
        events:
            Async iterator of ``LlmEvent`` objects from any LLM driver.

        Yields
        ------
        RenderEvent
            When ``show_intermediate=True``, ``TextRenderEvent(is_final=False)``
            is emitted for any text that precedes a tool call (inter-tool text).
            ``ToolSummaryRenderEvent`` mid-turn (throttled) and at turn end
            (unconditional), followed by ``TextRenderEvent(is_final=True)`` at
            turn end.
        """
        self._mark_consumed()
        _result_received = False
        async for event in events:
            if isinstance(event, TextLlmEvent):
                self._pending_text += event.text

            elif isinstance(event, ToolUseLlmEvent):
                async for render_event in self._handle_tool_event(event):
                    yield render_event

            else:  # ResultLlmEvent
                _result_received = True
                if self._has_any_tool_events():
                    yield self._emit_snapshot(is_complete=True)
                # On error with no streamed text, fall back to backend's
                # reported error so the adapter surfaces something
                # actionable instead of a bare "❌".
                final_text = self._pending_text or (
                    event.error_text if event.is_error and event.error_text else ""
                )
                yield TextRenderEvent(
                    text=final_text,
                    is_final=True,
                    is_error=event.is_error,  # #392: propagate error state
                )

        # Stream ended without ResultLlmEvent (truncation or upstream error)
        if not _result_received:
            if self._has_any_tool_events():
                yield self._emit_snapshot(is_complete=True)
            if self._pending_text:
                yield TextRenderEvent(text=self._pending_text, is_final=False)
            elif not self._has_any_tool_events():
                # No text, no tools, no result — backend died before producing
                # anything (e.g. auth failure, crash).  Emit an error event so
                # the adapter replaces the "…" placeholder instead of leaving
                # it stuck forever.
                _upstream_error = getattr(events, "error", None)
                yield TextRenderEvent(
                    text=str(_upstream_error)
                    if _upstream_error
                    else ("Something went wrong. Please try again."),
                    is_final=True,
                    is_error=True,
                )

    async def _handle_tool_event(
        self, event: ToolUseLlmEvent
    ) -> AsyncGenerator[RenderEvent, None]:
        """Handle a single ``ToolUseLlmEvent``, yielding any resulting ``RenderEvent``s.

        Flushes pending text as an intermediate event when ``show_intermediate``
        is enabled, then accumulates the tool call and emits a throttled
        ``ToolSummaryRenderEvent`` if the window has elapsed.

        When intermediate text is flushed, the ``ToolSummaryRenderEvent`` is
        intentionally skipped for this iteration so adapters have time to display
        the text before the tool card overwrites it.  The summary will still be
        emitted by the next tool event (once the throttle window elapses) or
        unconditionally by the final ``ResultLlmEvent``.
        """
        # Flush any text accumulated before this tool call so adapters
        # can show inter-tool text progressively (show_intermediate gate).
        flushed_intermediate = False
        if self._show_intermediate and self._pending_text:
            yield TextRenderEvent(text=self._pending_text, is_final=False)
            self._pending_text = ""
            flushed_intermediate = True
        self._accumulate(event)
        # Skip the immediate tool snapshot when we just flushed intermediate text —
        # emitting both back-to-back causes adapters to overwrite the text with the
        # tool card before the user can see it.
        if (
            not flushed_intermediate
            and self._should_emit()
            and self._has_any_tool_events()
        ):
            yield self._emit_snapshot()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mark_consumed(self) -> None:
        """Guard against reuse — raise if process() was already called."""
        if self._consumed:
            raise RuntimeError(
                "StreamProcessor.process() called more than once. "
                "Create a new instance per turn."
            )
        self._consumed = True

    def _accumulate_web(
        self, event: ToolUseLlmEvent, *, show_key: str, input_key: str
    ) -> None:
        """Append a web tool input value if the show flag is set."""
        if self._config.show.get(show_key, False):
            self._web_fetches.append(event.input.get(input_key, ""))

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

        elif tool_key in ("web_fetch", "webfetch"):
            self._accumulate_web(event, show_key="web_fetch", input_key="url")

        elif tool_key in ("web_search", "websearch"):
            self._accumulate_web(event, show_key="web_search", input_key="query")

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

    def _emit_snapshot(self, *, is_complete: bool = False) -> ToolSummaryRenderEvent:
        """Emit a ``ToolSummaryRenderEvent`` from a safe copy of all accumulators.

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
