"""StreamToolHandler: tool-event sub-handler for StreamProcessor.

Extracted from StreamProcessor (Slice 3 / #1282) to isolate tool-call lifecycle
logic from text/reasoning dispatch.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from factory.core.messaging.events import (
    ToolResultLlmEvent,
    ToolUseDeltaLlmEvent,
    ToolUseEndLlmEvent,
    ToolUseLlmEvent,
)
from factory.core.messaging.render_events import (
    RenderEvent,
    TextEndRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)
from factory.core.processors.stream_close import StreamCloseHandler
from factory.streaming.state_machine import StateMachine

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
# ``is_sensitive: bool`` flag if richer rendering is needed.
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


class StreamToolHandler:
    """Handle tool-use lifecycle events (ToolUse, Delta, End, Result).

    Parameters
    ----------
    sm_tool:
        StateMachine tracking open tool calls.
    tool_id_to_name:
        Mutable map from tool_call_id to the tool name (used for result
        sanitisation and cross-event correlation).
    close_handler:
        Reference to the close handler for reasoning block cleanup.
    sm_text:
        StateMachine tracking open text blocks (needed to close text on tool
        transitions).
    """

    def __init__(
        self,
        sm_tool: StateMachine[str, str],
        tool_id_to_name: dict[str, str],
        close_handler: StreamCloseHandler | None,
        sm_text: StateMachine[str, str],
    ) -> None:
        self._sm_tool = sm_tool
        self._tool_id_to_name = tool_id_to_name
        self._close_handler = close_handler
        self._sm_text = sm_text

    def handle_tool_use(self, event: ToolUseLlmEvent) -> Iterator[RenderEvent]:
        """Emit ToolCallStart; register open tool call via _sm_tool."""
        if self._close_handler is not None:
            yield from self._close_handler.close_reasoning_if_open()
        # ───── Slice 2 (#1099) v2 Text triplet — close open block ─────
        open_text = next(iter(self._sm_text.open_blocks), None)
        if open_text is not None:
            yield TextEndRenderEvent(message_id=open_text)
            self._sm_text.close(open_text)
        self._sm_tool.open(event.tool_id, event.tool_name)
        self._tool_id_to_name[event.tool_id] = event.tool_name
        yield ToolCallStartRenderEvent(
            tool_call_id=event.tool_id,
            tool_name=event.tool_name,
            input=event.input if event.input else None,
        )

    def handle_tool_use_delta(
        self, event: ToolUseDeltaLlmEvent
    ) -> Iterator[RenderEvent]:
        """Emit ToolCallArgs."""
        if self._close_handler is not None:
            yield from self._close_handler.close_reasoning_if_open()
        yield ToolCallArgsRenderEvent(
            tool_call_id=event.tool_id, delta=event.partial_json
        )

    def handle_tool_use_end(self, event: ToolUseEndLlmEvent) -> Iterator[RenderEvent]:
        """Emit ToolCallEnd; close open tool call in _sm_tool."""
        if self._close_handler is not None:
            yield from self._close_handler.close_reasoning_if_open()
        self._sm_tool.close(event.tool_id)
        yield ToolCallEndRenderEvent(tool_call_id=event.tool_id)

    def handle_tool_result(self, event: ToolResultLlmEvent) -> Iterator[RenderEvent]:
        """Emit ToolCallResult with sanitized content."""
        if self._close_handler is not None:
            yield from self._close_handler.close_reasoning_if_open()
        tool_name = self._tool_id_to_name.get(event.tool_id)
        yield ToolCallResultRenderEvent(
            tool_call_id=event.tool_id,
            content=_sanitize_tool_result_content(event.content, tool_name),
            is_error=event.is_error,
        )

    def synth_orphan_tool_ends(
        self,
    ) -> Iterator[ToolCallEndRenderEvent]:
        """Synthesize ``ToolCallEndRenderEvent`` for any open tool_call_ids.

        Called when a run terminates (``ResultLlmEvent``, truncation, or
        exception). Tool calls that started but never received a
        ``content_block_stop`` leave adapters with a dangling open-call card;
        the synthesized end closes it. WARN-logged once per
        orphan with a truncated id (last 6 chars) so cross-session correlation
        of the full opaque tool_call_id is not exposed in shared log
        aggregation (#1100 review S2).
        """
        # sorted() materialises keys before iteration; closing inside is safe.
        for tid in sorted(self._sm_tool.open_blocks):
            log.warning(
                (
                    "StreamToolHandler: synthesizing orphan ToolCallEnd "
                    "for tool_call_id=…%s"
                ),
                tid[-6:],
            )
            yield ToolCallEndRenderEvent(tool_call_id=tid)
            self._sm_tool.close(tid)


__all__ = ["StreamToolHandler", "_sanitize_tool_result_content"]
