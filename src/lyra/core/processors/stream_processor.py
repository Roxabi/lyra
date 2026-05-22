"""Channel-agnostic StreamProcessor: LlmEvent → RenderEvent pipeline.

Consumes an async stream of ``LlmEvent`` objects (from any LLM driver) and
produces ``RenderEvent`` objects consumed by outbound adapters (Telegram,
Discord, TTS tee, turn logger).

Pipeline contract (v2)
-----------------------
- ``TextLlmEvent``    → emit ``TextStartRenderEvent`` on first chunk, then one
                         ``TextDeltaRenderEvent`` per chunk, then
                         ``TextEndRenderEvent`` at block boundary.
- ``ToolUseLlmEvent`` → emit ``ToolCallStartRenderEvent`` / ``ToolCallArgsRenderEvent``
                         / ``ToolCallEndRenderEvent`` lifecycle events.
- ``ResultLlmEvent``  → close open text block (``TextEndRenderEvent``), synthesize
                         orphan ``ToolCallEnd`` events, emit ``RunFinishedRenderEvent``.

Hexagonal boundary
------------------
No imports from ``aiogram``, ``discord``, or ``anthropic`` are permitted here.
Only stdlib and lyra-internal modules may be used.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from typing import assert_never
from uuid import uuid4

from lyra.core.messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
    ThinkingLlmEvent,
    ToolResultLlmEvent,
    ToolUseDeltaLlmEvent,
    ToolUseEndLlmEvent,
    ToolUseLlmEvent,
)
from lyra.core.messaging.render_events import (
    FileEditSummary,
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
    RenderEvent,
    RunErrorRenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)
from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.core.trace import TraceContext

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


def _mint_text_block_id() -> str:
    """Per-block message id for the v2 Text triplet (Slice 2, #1099).

    Format: ``"text-<12-char-hex>"``. Mirrors the ``synthetic-<uuid4>`` shape
    used by ``run_id`` minting; distinguishable via prefix. Module-level helper
    matching the ``_sanitize_tool_result_content`` precedent below.
    """
    return f"text-{uuid4().hex[:12]}"


def _mint_reasoning_block_id() -> str:
    """Per-block message id for a reasoning block (Slice 4, #1101).

    Format: ``"reasoning-<12-char-hex>"``. Mirrors ``_mint_text_block_id``
    — same uuid4 approach, distinguishable via prefix.
    """
    return f"reasoning-{uuid4().hex[:12]}"


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

    Implements (duck-typed) the ``lyra.streaming.Parser[LlmEvent, RenderEvent]``
    Protocol via ``process`` (maps to ``feed``), ``finalize``, and ``is_done``.
    Composed, not inherited — see spec #1282 §Breadboard.

    Parameters
    ----------
    config:
        Controls display thresholds, bash truncation, throttle window, and
        which tool names surface in the summary card.
    show_intermediate:
        When ``True`` (default), ``TextDeltaRenderEvent`` chunks are emitted
        as they arrive so adapters can display text progressively.
        When ``False``, text is silently accumulated and closed via
        ``TextEndRenderEvent`` at the end of the block with no intermediate
        deltas.
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

        # --- Slice 2 (#1099) v2 Text triplet state ---
        # Tracks the message_id of the currently open text block (TextStart).
        # None when no text block is open. Set on TextStart, cleared on TextEnd.
        self._open_text_block_id: str | None = None

        # --- Slice 4 (#1101) Reasoning block state ---
        # Tracks the message_id of the currently open reasoning block.
        # None when no reasoning block is open. Set on first ThinkingLlmEvent
        # chunk, cleared when any non-Thinking event arrives (or on
        # truncation / exception). Defensive-close guards on every
        # non-Thinking branch ensure the block is always properly terminated.
        self._open_reasoning_block_id: str | None = None

        # --- reuse guard ---
        self._consumed: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def process(  # noqa: C901, PLR0915 — DEBT:wiring-bootstrap-deps — event-type dispatch + terminal fallbacks
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
            **v2 Text triplet (Slice 2, #1099):** for each contiguous text block,
            emits ``TextStartRenderEvent`` (once, on first chunk), then one
            ``TextDeltaRenderEvent`` per chunk, then ``TextEndRenderEvent`` at the
            block boundary (ToolUse, ResultLlmEvent, truncation, or exception).
            Interleaved text→tool→text sequences produce multiple bracketed blocks
            each with an independent ``message_id``.

            ``ToolCallStartRenderEvent`` / ``ToolCallArgsRenderEvent`` /
            ``ToolCallEndRenderEvent`` are emitted for tool-call lifecycle.
            ``RunStartedRenderEvent`` opens the turn; ``RunFinishedRenderEvent``
            closes it.
        """
        self._mark_consumed()
        run_id = TraceContext.get_trace_id() or f"synthetic-{TraceContext.generate()}"
        yield RunStartedRenderEvent(run_id=run_id)
        _result_received = False
        # Soft-error capture: ResultLlmEvent.is_error=True signals the LLM
        # backend returned an error response. Carried out of the try block so
        # the post-finally emission can choose RunErrorRenderEvent vs
        # RunFinishedRenderEvent. error_text is driver-curated user-facing
        # text (e.g. "Not logged in · Please run /login"), not an exception
        # str() — safe to forward on the NATS bus.
        _result_is_error = False
        _result_error_text: str | None = None
        try:
            async for event in events:
                if isinstance(event, TextLlmEvent):
                    # ───── Slice 4 (#1101) reasoning-close guard ─────
                    for _re in self._close_reasoning_if_open():
                        yield _re
                    self._pending_text += event.text
                    self._total_text += event.text
                    # ───── Slice 2 (#1099) v2 Text triplet ─────
                    if self._open_text_block_id is None:
                        self._open_text_block_id = _mint_text_block_id()
                        yield TextStartRenderEvent(message_id=self._open_text_block_id)
                    yield TextDeltaRenderEvent(
                        message_id=self._open_text_block_id,
                        delta=event.text,
                    )

                elif isinstance(event, ToolUseLlmEvent):
                    # ───── Slice 4 (#1101) reasoning-close guard ─────
                    for _re in self._close_reasoning_if_open():
                        yield _re
                    # ───── Slice 2 (#1099) v2 Text triplet — close open block ─────
                    if self._open_text_block_id is not None:
                        yield TextEndRenderEvent(message_id=self._open_text_block_id)
                        self._open_text_block_id = None
                    async for render_event in self._handle_tool_event(event):
                        yield render_event

                elif isinstance(event, ToolUseDeltaLlmEvent):
                    # ───── Slice 4 (#1101) reasoning-close guard ─────
                    for _re in self._close_reasoning_if_open():
                        yield _re
                    yield ToolCallArgsRenderEvent(
                        tool_call_id=event.tool_id, delta=event.partial_json
                    )

                elif isinstance(event, ToolUseEndLlmEvent):
                    # ───── Slice 4 (#1101) reasoning-close guard ─────
                    for _re in self._close_reasoning_if_open():
                        yield _re
                    self._open_tool_call_ids.discard(event.tool_id)
                    yield ToolCallEndRenderEvent(tool_call_id=event.tool_id)

                elif isinstance(event, ToolResultLlmEvent):
                    # ───── Slice 4 (#1101) reasoning-close guard ─────
                    for _re in self._close_reasoning_if_open():
                        yield _re
                    tool_name = self._tool_id_to_name.get(event.tool_id)
                    yield ToolCallResultRenderEvent(
                        tool_call_id=event.tool_id,
                        content=_sanitize_tool_result_content(event.content, tool_name),
                        is_error=event.is_error,
                    )

                elif isinstance(event, ResultLlmEvent):  # pyright: ignore[reportUnnecessaryIsInstance] — DEBT:defensive-narrow-payloads
                    _result_received = True
                    _result_is_error = event.is_error
                    _result_error_text = event.error_text
                    # ───── Slice 4 (#1101) reasoning-close guard ─────
                    for _re in self._close_reasoning_if_open():
                        yield _re
                    # ───── Slice 2 (#1099) v2 Text triplet — close open block ─────
                    if self._open_text_block_id is not None:
                        yield TextEndRenderEvent(message_id=self._open_text_block_id)
                        self._open_text_block_id = None
                    # Synthesize ToolCallEnd for any open tool_call_ids that
                    # never received a content_block_stop (truncated stream,
                    # partial tool call). Loud WARN log per orphan.
                    for orphan_event in self._synth_orphan_tool_ends():
                        yield orphan_event

                elif isinstance(event, ThinkingLlmEvent):  # pyright: ignore[reportUnnecessaryIsInstance]
                    # ───── Slice 4 (#1101) reasoning block emission ─────
                    # SC-6 (spec v2 lines 65, 260): drop the chunk entirely
                    # when show_intermediate=False — no Reasoning* produced,
                    # no v1 intermediate.
                    if not self._show_intermediate:
                        continue
                    if self._open_reasoning_block_id is None:
                        self._open_reasoning_block_id = _mint_reasoning_block_id()
                        log.debug(
                            "reasoning block opened message_id=%s",
                            self._open_reasoning_block_id,
                        )
                        yield ReasoningStartRenderEvent(
                            message_id=self._open_reasoning_block_id
                        )
                    yield ReasoningDeltaRenderEvent(
                        message_id=self._open_reasoning_block_id,
                        delta=event.text,
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
                # ───── Slice 4 (#1101) orphan reasoning-close (truncated stream) ─────
                if self._open_reasoning_block_id is not None:
                    log.warning(
                        "StreamProcessor: orphan ReasoningEnd synthesis "
                        "(truncated stream) message_id=…%s",
                        self._open_reasoning_block_id[-6:],
                    )
                    yield ReasoningEndRenderEvent(
                        message_id=self._open_reasoning_block_id
                    )
                    self._open_reasoning_block_id = None
                # ───── Slice 2 (#1099) v2 Text triplet — close open block ─────
                if self._open_text_block_id is not None:
                    yield TextEndRenderEvent(message_id=self._open_text_block_id)
                    self._open_text_block_id = None
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
            # ───── Slice 4 (#1101) orphan reasoning-close (exception) ─────
            if self._open_reasoning_block_id is not None:
                log.warning(
                    "StreamProcessor: orphan ReasoningEnd synthesis (exception) "
                    "message_id=…%s",
                    self._open_reasoning_block_id[-6:],
                )
                yield ReasoningEndRenderEvent(message_id=self._open_reasoning_block_id)
                self._open_reasoning_block_id = None
            # ───── Slice 2 (#1099) v2 Text triplet — close open block ─────
            if self._open_text_block_id is not None:
                yield TextEndRenderEvent(message_id=self._open_text_block_id)
                self._open_text_block_id = None
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
        if _result_is_error:
            # Soft error: LLM backend returned an error response. The run
            # completed cleanly (no infrastructure exception), but the user
            # should still see an ❌ prefix on the rendered message. Emit
            # RunErrorRenderEvent instead of RunFinishedRenderEvent so the
            # adapter's dispatch ladder can flag the turn as error.
            # message is driver-curated user-facing text (ResultLlmEvent.
            # error_text), not str(exception) — safe for NATS broadcast.
            yield RunErrorRenderEvent(
                run_id=run_id,
                message=str(_result_error_text or "model_error"),
                code=None,
            )
        else:
            yield RunFinishedRenderEvent(run_id=run_id, outcome="success")

    async def _handle_tool_event(
        self, event: ToolUseLlmEvent
    ) -> AsyncGenerator[RenderEvent, None]:
        """Handle a single ``ToolUseLlmEvent``, yielding any resulting ``RenderEvent``s.

        Slice 3 (#1100) emits ``ToolCallStartRenderEvent`` with cross-event
        ``tool_call_id`` correlator. The parser already deduplicates the dual
        emission of ``ToolUseLlmEvent`` (streaming + post-hoc paths), so this
        handler sees each ``tool_id`` exactly once.

        Flushes pending text state when ``show_intermediate`` is enabled,
        then accumulates the tool call into internal accumulators.
        """
        self._open_tool_call_ids.add(event.tool_id)
        self._tool_id_to_name[event.tool_id] = event.tool_name
        yield ToolCallStartRenderEvent(
            tool_call_id=event.tool_id, tool_name=event.tool_name
        )

        # Clear per-segment pending text buffer on each tool event.
        if self._show_intermediate:
            self._pending_text = ""
        self._accumulate(event)

    def _close_reasoning_if_open(self) -> Iterator[ReasoningEndRenderEvent]:
        """Yield ``ReasoningEndRenderEvent`` and clear state if a block is open.

        Defensive guard called at the top of every non-Thinking LlmEvent branch
        (T10 / Slice 4, #1101). In normal model output the LLM closes all
        thinking blocks before emitting text or tool calls; this guard handles
        any interleaving that slips through (architect review B).
        """
        if self._open_reasoning_block_id is not None:
            log.debug(
                "reasoning block closed message_id=%s",
                self._open_reasoning_block_id,
            )
            yield ReasoningEndRenderEvent(message_id=self._open_reasoning_block_id)
            self._open_reasoning_block_id = None

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
