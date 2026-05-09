"""Channel-agnostic StreamProcessor: LlmEvent → RenderEvent pipeline.

Consumes an async stream of ``LlmEvent`` objects (from any LLM driver) and
produces ``RenderEvent`` objects consumed by outbound adapters (Telegram,
Discord, TTS tee, turn logger).

Pipeline contract
-----------------
- ``TextLlmEvent``    → stream each chunk as ``TextRenderEvent(is_final=False)``
                         immediately; also accumulate in ``_total_text`` for the
                         final ``TextRenderEvent(is_final=True)``
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

import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from typing import assert_never

from lyra.core.messaging.error_extractor import _extract_worker_error
from lyra.core.messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
    ToolResultLlmEvent,
    ToolUseDeltaLlmEvent,
    ToolUseEndLlmEvent,
    ToolUseLlmEvent,
)
from lyra.core.messaging.metrics import emit_received_total
from lyra.core.messaging.render_events import (
    FileEditSummary,
    RenderEvent,
    RunErrorRenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    SilentCounts,
    TextRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
    ToolSummaryRenderEvent,
)
from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.core.trace import TraceContext
from roxabi_contracts.errors import KNOWN_CODES

log = logging.getLogger(__name__)

# Slice 3 (#1100 review) — security boundary for ToolCallResultRenderEvent.
# ``ToolResultLlmEvent.content`` carries raw tool output (file contents, shell
# stdout, web fetch responses, etc.). The output is published on the NATS bus
# where any subscriber can read it. Mirror the ``RunErrorRenderEvent``
# discipline: secure-by-default — redact unless explicitly safe.
#
# Rationale (per #1131 iter-2 review): an inclusion list of "sensitive" names
# leaves any future tool (MCP servers, ``WebFetch`` to a metadata endpoint,
# third-party plugins) leaking credentials unredacted. The allowlist below
# names tools that return path-only / structural data — never file content,
# shell output, or arbitrary network responses. Everything else is redacted
# by default. Slice 5 (#1102) is the natural place to refine with a per-tool
# ``is_sensitive: bool`` flag in ``ToolDisplayConfig`` if richer rendering is
# needed.
_NON_SENSITIVE_TOOL_NAMES: frozenset[str] = frozenset(
    {"glob", "grep", "ls", "todoread", "todowrite"}
)
_MAX_CONTENT_BYTES = 65_536
_REDACTED_PLACEHOLDER = "[redacted — tool output suppressed for security]"
_TRUNCATED_SENTINEL = "…[truncated]"


def _sanitize_tool_result_content(content: str, tool_name: str | None) -> str:
    """Apply secret-leak guard + size cap to ``ToolCallResultRenderEvent.content``.

    Secure-by-default: returns the redaction placeholder UNLESS the tool name
    is in the explicit ``_NON_SENSITIVE_TOOL_NAMES`` allowlist (path-only or
    structural-data tools). For allowlisted tools, content passes through with
    only the size cap applied.

    ``tool_name`` is ``None`` when the parser receives a ``tool_result`` block
    whose ``tool_use_id`` did not correlate with a prior ``ToolUseLlmEvent``
    (orphan result). Treated as sensitive — fail-closed.
    """
    if tool_name is None or tool_name.lower() not in _NON_SENSITIVE_TOOL_NAMES:
        return _REDACTED_PLACEHOLDER
    encoded = content.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_CONTENT_BYTES:
        return content
    sentinel_bytes = len(_TRUNCATED_SENTINEL.encode("utf-8"))
    truncated = encoded[: _MAX_CONTENT_BYTES - sentinel_bytes].decode(
        "utf-8", errors="ignore"
    )
    return f"{truncated}{_TRUNCATED_SENTINEL}"


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
        self._total_text: str = ""  # full accumulated text for final emit

        # --- Slice 3 (#1100) ToolCall* lifecycle state ---
        # Open-call tracker for orphan ``ToolCallEnd`` synthesis. Populated on
        # ``ToolCallStart`` emission, cleared on ``ToolCallEnd``. Anything still
        # in the set at ``ResultLlmEvent`` time gets a synthesized end event.
        # Dedupe of duplicate ``ToolUseLlmEvent``s lives in the parser
        # (``cli_streaming_parser._emitted_tool_use_ids``) — wire-level
        # artifacts of the Anthropic CLI stay below the application boundary.
        self._open_tool_call_ids: set[str] = set()
        # tool_id → tool_name map populated on ``ToolUseLlmEvent`` so the
        # ``_sanitize_tool_result_content`` boundary scrubber can decide whether
        # to redact based on tool name (Read/Bash/Edit/Write are sensitive).
        self._tool_id_to_name: dict[str, str] = {}

        # --- reuse guard ---
        self._consumed: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def process(  # noqa: C901, PLR0915 — event-type dispatch + terminal fallbacks
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
            ``TextRenderEvent(is_final=False)`` is emitted for EVERY text chunk
            as it arrives, enabling real-time streaming to adapters (1 s debounce).
            When ``show_intermediate=True``, the per-segment buffer is cleared on
            each tool call so the tool snapshot does not overwrite streamed text.
            ``ToolSummaryRenderEvent`` mid-turn (throttled) and at turn end
            (unconditional), followed by ``TextRenderEvent(is_final=True)`` at
            turn end (using the full ``_total_text`` accumulator).
        """
        self._mark_consumed()
        run_id = TraceContext.get_trace_id() or f"synthetic-{TraceContext.generate()}"
        yield RunStartedRenderEvent(run_id=run_id)
        _result_received = False
        try:
            async for event in events:
                if isinstance(event, TextLlmEvent):
                    self._pending_text += event.text
                    self._total_text += event.text
                    # Stream each chunk progressively so adapters can
                    # edit the placeholder in real time (1 s debounce).
                    yield TextRenderEvent(text=event.text, is_final=False)

                elif isinstance(event, ToolUseLlmEvent):
                    async for render_event in self._handle_tool_event(event):
                        yield render_event

                elif isinstance(event, ToolUseDeltaLlmEvent):
                    yield ToolCallArgsRenderEvent(
                        tool_call_id=event.tool_id, delta=event.partial_json
                    )

                elif isinstance(event, ToolUseEndLlmEvent):
                    self._open_tool_call_ids.discard(event.tool_id)
                    yield ToolCallEndRenderEvent(tool_call_id=event.tool_id)

                elif isinstance(event, ToolResultLlmEvent):
                    tool_name = self._tool_id_to_name.get(event.tool_id)
                    yield ToolCallResultRenderEvent(
                        tool_call_id=event.tool_id,
                        content=_sanitize_tool_result_content(event.content, tool_name),
                        is_error=event.is_error,
                    )

                elif isinstance(event, ResultLlmEvent):  # pyright: ignore[reportUnnecessaryIsInstance]
                    _result_received = True
                    # Synthesize ToolCallEnd for any open tool_call_ids that
                    # never received a content_block_stop (truncated stream,
                    # partial tool call). Loud WARN log per orphan.
                    for orphan_event in self._synth_orphan_tool_ends():
                        yield orphan_event
                    if self._has_any_tool_events():
                        yield self._emit_snapshot(is_complete=True)
                    # On error with no streamed text, surface the structured WorkerError
                    # message (P1 path) or the legacy error_text shim (P2 transitional).
                    if event.is_error and not self._total_text:
                        we = _extract_worker_error(event)
                        if we is not None:
                            meta = KNOWN_CODES.get(we.code)
                            domain = meta.domain if meta else we.code.split(".")[0]
                            emit_received_total(code=we.code, domain=domain)
                            error_text = we.message
                        else:
                            # P2 transitional: fall back to legacy error_text shim.
                            error_text = event.error_text or ""
                        final_text = error_text
                    else:
                        final_text = self._total_text
                    yield TextRenderEvent(
                        text=final_text,
                        is_final=True,
                        is_error=event.is_error,  # #392: propagate error state
                    )

                else:
                    # Cross-slice invariant 3: no silent event drop. When the
                    # ``LlmEvent`` union widens (e.g. Slice 4 reasoning events)
                    # without updating this dispatch, ``assert_never`` surfaces
                    # the gap at pyright time AND raises ``AssertionError`` at
                    # runtime so a missing branch is never silently swallowed.
                    assert_never(event)

            # Stream ended without ResultLlmEvent (truncation or upstream error)
            if not _result_received:
                if self._has_any_tool_events():
                    yield self._emit_snapshot(is_complete=True)
                if self._total_text:
                    yield TextRenderEvent(text=self._total_text, is_final=False)
                elif not self._has_any_tool_events():
                    # No text, no tools, no result — backend died before producing
                    # anything (e.g. auth failure, crash).  Emit an error event so
                    # the adapter replaces the "…" placeholder instead of leaving
                    # it stuck forever.
                    _upstream_error = getattr(events, "error", None)
                    yield TextRenderEvent(
                        text=str(_upstream_error) if _upstream_error else "",
                        is_final=True,
                        is_error=True,
                    )
        except Exception as exc:
            # Slice 1 (#1098): infrastructure-level exception during stream
            # processing. Surface a RunErrorRenderEvent then re-raise so the
            # adapter's existing exception handler (sets stream_error and
            # falls through to classify_stream_error) keeps working.
            #
            # message=type(exc).__name__ — never str(exc): exception strings can
            # carry file paths, internal hostnames, auth-token fragments from
            # httpx/aiohttp errors, DB connection strings, etc. RunErrorRenderEvent
            # is published on the NATS bus where any subscriber can read it.
            yield RunErrorRenderEvent(
                run_id=run_id, message=type(exc).__name__, code=None
            )
            raise
        finally:
            # Eagerly finalize the input iterator on both success and exception
            # paths so generators holding resources (e.g. CLI subprocess pipes)
            # release them deterministically rather than on GC.
            _aclose = getattr(events, "aclose", None)
            if _aclose is not None:
                await _aclose()
        yield RunFinishedRenderEvent(run_id=run_id, outcome="success")

    async def _handle_tool_event(
        self, event: ToolUseLlmEvent
    ) -> AsyncGenerator[RenderEvent, None]:
        """Handle a single ``ToolUseLlmEvent``, yielding any resulting ``RenderEvent``s.

        Slice 3 (#1100) emits ``ToolCallStartRenderEvent`` with cross-event
        ``tool_call_id`` correlator. The parser already deduplicates the dual
        emission of ``ToolUseLlmEvent`` (streaming + post-hoc paths), so this
        handler sees each ``tool_id`` exactly once.

        Flushes pending text as an intermediate event when ``show_intermediate``
        is enabled, then accumulates the tool call (v1 ``ToolSummaryRenderEvent``
        dual-emit, kept until Slice 5).
        """
        self._open_tool_call_ids.add(event.tool_id)
        self._tool_id_to_name[event.tool_id] = event.tool_name
        yield ToolCallStartRenderEvent(
            tool_call_id=event.tool_id, tool_name=event.tool_name
        )

        # Flush any text accumulated before this tool call so adapters
        # can show inter-tool text progressively (show_intermediate gate).
        flushed_intermediate = False
        if self._show_intermediate and self._pending_text:
            # Text was already streamed chunk-by-chunk in process(); just
            # clear the per-segment buffer and mark as flushed so the
            # immediate tool snapshot is suppressed (avoids overwriting
            # the streamed text with the tool card before the user sees it).
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

    def _synth_orphan_tool_ends(
        self,
    ) -> Iterator[ToolCallEndRenderEvent]:
        """Synthesize ``ToolCallEndRenderEvent`` for any open tool_call_ids.

        Called at ``ResultLlmEvent`` time. Tool calls that started but never
        received a ``content_block_stop`` leave adapters with a dangling
        open-call card; the synthesized end closes it. WARN-logged once per
        orphan with a truncated id (last 6 chars) so cross-session correlation
        of the full opaque tool_call_id is not exposed in shared log
        aggregation (#1100 review S2).
        """
        for tid in sorted(self._open_tool_call_ids):
            log.warning(
                "StreamProcessor: synthesizing orphan ToolCallEnd for tool_call_id=…%s",
                tid[-6:],
            )
            yield ToolCallEndRenderEvent(tool_call_id=tid)
        self._open_tool_call_ids.clear()

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
