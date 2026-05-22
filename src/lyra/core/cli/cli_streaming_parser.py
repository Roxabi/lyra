"""Pure JSON event parser for CLI streaming protocol.

Extracted from cli_streaming.py — parses NDJSON lines into LlmEvents
without any I/O or async concerns.
"""

from __future__ import annotations

import json
import logging
from collections import deque

from roxabi_contracts.errors import KNOWN_CODES, WorkerError

from ..messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
    ThinkingLlmEvent,
    ToolResultLlmEvent,
    ToolUseDeltaLlmEvent,
    ToolUseEndLlmEvent,
    ToolUseLlmEvent,
)
from ..messaging.metrics import emit_populated_total

# Anthropic CLI NDJSON wire constants — kept here so the parser is the single
# source of truth on what shapes upstream emits.
_DELTA_INPUT_JSON = "input_json_delta"
_BLOCK_TYPE_TOOL_USE = "tool_use"
_BLOCK_TYPE_TOOL_RESULT = "tool_result"
_BLOCK_TYPE_THINKING = "thinking"
_DELTA_THINKING = "thinking_delta"
_DELTA_SIGNATURE = "signature_delta"

log = logging.getLogger(__name__)

# Subtypes that indicate an auth failure from the upstream CLI.
_AUTH_SUBTYPES = frozenset({"auth_error", "auth", "login_required"})
# Subtypes that suggest a lost / unresumable session.
_SESSION_LOST_SUBTYPES = frozenset({"session_expired", "session_lost", "resume_failed"})

# Max length of bus-bound CLI error messages. Upstream wire content is
# unbounded; trim before publishing to keep the NATS payload predictable.
_BUS_BOUND_MESSAGE_MAX_LEN = 200


def _scrub_cli_error_text(text: str) -> str:
    """Bound length + strip control chars from bus-bound CLI error text.

    #1252 (sibling of #1212/#1215/#1219): path (a) — when the CLI reports
    ``is_error=True`` — sources error text verbatim from upstream wire
    fields (``result.errors[0]`` / ``result.result``). Without scrubbing,
    arbitrary bytes propagate to ``WorkerError.message`` and
    ``ResultLlmEvent.error_text`` (both bus-bound). Full diagnostic stays
    in ``log.warning`` at the call site.
    """
    if not text:
        return text
    scrubbed = "".join(c if c.isprintable() else " " for c in text)
    if len(scrubbed) > _BUS_BOUND_MESSAGE_MAX_LEN:
        scrubbed = scrubbed[: _BUS_BOUND_MESSAGE_MAX_LEN - 1] + "…"
    return scrubbed


def _classify_cli_error(subtype: str, error_text: str) -> WorkerError:
    """Map a CLI result subtype + message to a structured WorkerError.

    Mapping rules (path a — upstream CLI reports is_error):
      * auth-related subtype  → cli.auth   (not retryable)
      * session-related       → cli.session_lost (retryable)
      * anything else         → cli.parse  (not retryable)

    ``worker.parse`` is NOT present in KNOWN_CODES (registry only has
    ``cli.parse`` and ``transport.parse``), so parser failures use
    ``cli.parse`` per ADR-066 fallback policy.

    ``error_text`` is scrubbed via :func:`_scrub_cli_error_text` before
    landing in ``WorkerError.message`` — see #1252.
    """
    if subtype in _AUTH_SUBTYPES:
        code = "cli.auth"
    elif subtype in _SESSION_LOST_SUBTYPES:
        code = "cli.session_lost"
    else:
        code = "cli.parse"

    meta = KNOWN_CODES[code]
    return WorkerError(
        code=code,
        message=_scrub_cli_error_text(error_text) or meta.description,
        retryable=meta.default_retryable,
    )


class CliStreamingParser:
    """Pure JSON event parser for CLI streaming protocol.

    Parses NDJSON lines from the CLI subprocess stdout into LlmEvent objects.
    Maintains session state (session_id, error) across parse calls.

    Implements (duck-typed) the ``lyra.streaming.Parser[str, LlmEvent]`` Protocol
    via ``parse_line`` (maps to ``feed``), ``finalize``, and ``is_done``. Composed,
    not inherited — see spec #1282 §Breadboard.
    """

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        self.session_id: str | None = None
        self.error: str | None = None
        self._had_text_delta = False
        self._done = False
        self._pending: deque[LlmEvent] = deque()
        # Slice 3 (#1100): index → tool_id map for content_block_delta /
        # content_block_stop correlation. Only populated when the CLI provides
        # an `index` field on the content_block_start event for a tool_use
        # block. Text blocks and tool_use blocks without an index do not
        # participate in the args-streaming lifecycle.
        self._open_tool_blocks: dict[int, str] = {}
        # Slice 3 dedupe (#1100 review): tool_ids for which a ToolUseLlmEvent
        # has already been emitted. Both content_block_start and the post-hoc
        # assistant-message path can announce the same tool_use; the second
        # emission is silently dropped so downstream consumers see exactly one
        # ToolUseLlmEvent per tool_id.
        self._emitted_tool_use_ids: set[str] = set()
        # Slice 4 (#1101): index of the currently open thinking block, or None.
        # Set on content_block_start when block type == "thinking"; cleared on
        # content_block_stop. Only one thinking block open at a time per turn.
        self._open_thinking_index: int | None = None

    def parse_line(self, line: str) -> deque[LlmEvent]:  # noqa: C901, PLR0912, PLR0915 — DEBT:complexity-residual — protocol event dispatch
        """Parse a JSON line, update state, and return events to yield.

        Returns a deque of LlmEvent objects. Caller should pop from left.
        Sets self._done = True when result event is parsed.
        """
        if self._done:
            return self._pending

        if not line:
            return self._pending

        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            # Spec C4 path (b): a JSON-shaped line that fails to parse is a
            # protocol-level corruption (truncated stream, encoding bug, etc.)
            # → emit terminal `cli.parse` envelope so the hub instrumentation
            # chain activates. Non-JSON lines (debug output, blank lines) are
            # still silently skipped — the heuristic is "line starts with `{`".
            # CLI NDJSON protocol emits one object per line — never bare arrays
            # or scalars. Anything starting with `{` is unambiguously a protocol
            # line; debug lines use prefixes like `[DEBUG]`, `INFO:`, blank, etc.
            stripped = line.lstrip()
            if stripped.startswith("{"):
                # Sanitize bus-bound message (#1219, sibling of #1212/#1215).
                # JSONDecodeError.__str__ on current CPython is well-behaved,
                # but defense-in-depth keeps `.doc` content (the raw malformed
                # line) off the bus across Python versions and custom decoder
                # subclasses. Full diagnostic preserved in log.warning below.
                log.warning("CLI JSON parse error: %r", exc)
                meta = KNOWN_CODES["cli.parse"]
                worker_error = WorkerError(
                    code="cli.parse",
                    message=f"CLI emitted malformed JSON: {type(exc).__name__}",
                    retryable=meta.default_retryable,
                )
                emit_populated_total(domain="cli")
                self._done = True
                self._pending.append(
                    ResultLlmEvent(
                        is_error=True,
                        duration_ms=0,
                        cost_usd=None,
                        error_text=worker_error.message,
                        session_id=self.session_id,
                        worker_error=worker_error,
                    )
                )
            return self._pending

        msg_type = data.get("type", "")

        if msg_type == "system" and data.get("subtype") == "init":
            self.session_id = data.get("session_id", "") or None
            log.debug(
                "[pool:%s] streaming init: session=%s",
                self.pool_id,
                self.session_id,
            )

        elif msg_type == "assistant":
            blocks = data.get("message", {}).get("content", [])
            for b in blocks:
                if b.get("type") == _BLOCK_TYPE_TOOL_USE:
                    tool_id = b.get("id", "")
                    # Slice 3 dedupe (#1100 review): CLI emits the tool_use
                    # block twice — once at content_block_start (input={}) and
                    # again post-hoc here (input populated). Skip the post-hoc
                    # duplicate when the streaming path already announced this
                    # tool_id; the args dict is reconstructed from
                    # ToolUseDeltaLlmEvent stream downstream.
                    if tool_id and tool_id in self._emitted_tool_use_ids:
                        continue
                    if tool_id:
                        self._emitted_tool_use_ids.add(tool_id)
                    self._pending.append(
                        ToolUseLlmEvent(
                            tool_name=b.get("name", ""),
                            tool_id=tool_id,
                            input=b.get("input", {}),
                        )
                    )

        elif msg_type == "stream_event":
            event_data = data.get("event", data)
            event_type = event_data.get("type", "")
            if event_type == "content_block_start":
                cb = event_data.get("content_block", {})
                if cb.get("type") == _BLOCK_TYPE_THINKING:
                    idx = event_data.get("index")
                    if isinstance(idx, int):
                        self._open_thinking_index = idx
                    # No event emitted on start — emission happens on first delta.
                elif cb.get("type") == _BLOCK_TYPE_TOOL_USE:
                    tool_id = cb.get("id", "")
                    idx = event_data.get("index")
                    if isinstance(idx, int) and tool_id:
                        self._open_tool_blocks[idx] = tool_id
                    if tool_id and tool_id not in self._emitted_tool_use_ids:
                        self._emitted_tool_use_ids.add(tool_id)
                        self._pending.append(
                            ToolUseLlmEvent(
                                tool_name=cb.get("name", ""),
                                tool_id=tool_id,
                                input={},
                            )
                        )
            elif event_type == "content_block_delta":
                delta = event_data.get("delta", {})
                delta_type = delta.get("type", "")
                if delta_type == _DELTA_THINKING:
                    idx = event_data.get("index")
                    thinking = delta.get("thinking", "")
                    if (
                        isinstance(idx, int)
                        and idx == self._open_thinking_index
                        and thinking
                    ):
                        self._pending.append(ThinkingLlmEvent(text=thinking))
                elif delta_type == _DELTA_SIGNATURE:
                    pass  # API-replay metadata; no user-facing render
                elif delta_type == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        self._had_text_delta = True
                        self._pending.append(TextLlmEvent(text=text))
                elif delta_type == _DELTA_INPUT_JSON:
                    idx = event_data.get("index")
                    partial_json = delta.get("partial_json", "")
                    if (
                        isinstance(idx, int)
                        and idx in self._open_tool_blocks
                        and partial_json
                    ):
                        self._pending.append(
                            ToolUseDeltaLlmEvent(
                                tool_id=self._open_tool_blocks[idx],
                                partial_json=partial_json,
                            )
                        )
            elif event_type == "content_block_stop":
                idx = event_data.get("index")
                if isinstance(idx, int) and idx in self._open_tool_blocks:
                    self._pending.append(
                        ToolUseEndLlmEvent(tool_id=self._open_tool_blocks.pop(idx))
                    )
                if isinstance(idx, int) and idx == self._open_thinking_index:
                    self._open_thinking_index = None

        elif msg_type == "user":
            blocks = data.get("message", {}).get("content", [])
            for b in blocks:
                if b.get("type") != _BLOCK_TYPE_TOOL_RESULT:
                    continue
                content = b.get("content", "")
                # Anthropic CLI may pack content as a list of typed blocks.
                # Render text-only this slice; non-text blocks placeholder as
                # `[{type}]` so non-renderable payloads never silently vanish.
                if isinstance(content, list):
                    parts = []
                    for blk in content:
                        if isinstance(blk, dict):
                            if blk.get("type") == "text":
                                parts.append(str(blk.get("text", "")))
                            else:
                                parts.append(f"[{blk.get('type', '?')}]")
                    content = "".join(parts)
                self._pending.append(
                    ToolResultLlmEvent(
                        tool_id=b.get("tool_use_id", ""),
                        content=str(content),
                        is_error=bool(b.get("is_error", False)),
                    )
                )

        elif msg_type == "result":
            sid = data.get("session_id", "")
            if sid:
                self.session_id = sid
            is_error = data.get("is_error", False)
            subtype = data.get("subtype", "")
            # Classify based on observed stream, not self-reported flags.
            # CLI reports is_error=True + subtype="success" both when:
            #   (a) a tool call failed but the model recovered and
            #       streamed a valid answer via text_delta events, and
            #   (b) the CLI exited early (auth failure, crash) and
            #       emitted the error text only in the result field.
            # Only (a) should downgrade to success — and the signal for
            # that is whether any text was actually streamed.
            if is_error and subtype == "success" and self._had_text_delta:
                log.info(
                    "[pool:%s] streaming result is_error=True but"
                    " subtype=success with streamed text — treating"
                    " as success",
                    self.pool_id,
                )
                is_error = False
            elif is_error:
                errors = data.get("errors", [])
                self.error = (
                    errors[0]
                    if errors
                    else data.get("result") or subtype or "Unknown streaming error"
                )
                log.warning(
                    "[pool:%s] streaming result is_error=True"
                    " subtype=%s had_text_delta=%s duration_ms=%d"
                    " result=%r",
                    self.pool_id,
                    subtype,
                    self._had_text_delta,
                    data.get("duration_ms", 0),
                    (data.get("result") or "")[:200],
                )
            log.info(
                "[pool:%s] streaming result: %dms",
                self.pool_id,
                data.get("duration_ms", 0),
            )
            self._done = True
            # Path (a): upstream CLI emits is_error=True — classify to cli.* code.
            worker_error: WorkerError | None = None
            if is_error:
                worker_error = _classify_cli_error(subtype, self.error or "")
                emit_populated_total(domain="cli")
            self._pending.append(
                ResultLlmEvent(
                    is_error=is_error,
                    duration_ms=data.get("duration_ms", 0),
                    cost_usd=None,
                    # #1252: route the scrubbed message — not raw ``self.error``
                    # (upstream wire content) — to the bus, matching path (b).
                    error_text=worker_error.message if worker_error else None,
                    session_id=data.get("session_id") or None,
                    worker_error=worker_error,
                )
            )

        return self._pending
