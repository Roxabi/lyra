"""StreamCloseHandler: close open stream blocks on truncation / exception paths.

Extracted from StreamProcessor (Slice 3 / #1282) to isolate lifecycle-cleanup
logic from per-event dispatch logic.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from lyra.core.messaging.render_events import (
    ReasoningEndRenderEvent,
    RenderEvent,
    TextEndRenderEvent,
)
from lyra.streaming.state_machine import StateMachine

log = logging.getLogger(__name__)


class StreamCloseHandler:
    """Close open state-machine blocks and synthesize orphan events.

    Parameters
    ----------
    sm_text:
        StateMachine tracking open text blocks.
    sm_reasoning:
        StateMachine tracking open reasoning blocks.
    sm_tool:
        StateMachine tracking open tool calls.
    tool_id_to_name:
        Map from tool_call_id to the tool name (needed for result sanitisation,
        but kept here for symmetry with the other extracted helpers).
    tool_handler:
        Tool handler reference (reserved for future orphan-synthesis logic).
    """

    def __init__(
        self,
        sm_text: StateMachine[str, str],
        sm_reasoning: StateMachine[str, str],
        sm_tool: StateMachine[str, str],
        tool_id_to_name: dict[str, str],
        tool_handler: Any,
    ) -> None:
        self._sm_text = sm_text
        self._sm_reasoning = sm_reasoning
        self._sm_tool = sm_tool
        self._tool_id_to_name = tool_id_to_name
        self._tool_handler = tool_handler

    # ------------------------------------------------------------------
    # Public close helpers
    # ------------------------------------------------------------------

    def close_reasoning_if_open(self) -> Iterator[ReasoningEndRenderEvent]:
        """Yield ``ReasoningEndRenderEvent`` and clear state if a block is open.

        Defensive guard called at the top of every non-Thinking LlmEvent branch
        (T10 / Slice 4, #1101). In normal model output the LLM closes all
        thinking blocks before emitting text or tool calls; this guard handles
        any interleaving that slips through (architect review B).
        """
        open_reasoning = next(iter(self._sm_reasoning.open_blocks), None)
        if open_reasoning is not None:
            log.debug("reasoning block closed message_id=%s", open_reasoning)
            yield ReasoningEndRenderEvent(message_id=open_reasoning)
            self._sm_reasoning.close(open_reasoning)

    def close_truncated_stream(self) -> Iterator[RenderEvent]:
        """Close open blocks when the stream ends without a ResultLlmEvent.

        Handles truncation (upstream error / subprocess kill).
        """
        yield from self._close_open_blocks("truncated stream")

    def close_exception_path(self) -> Iterator[RenderEvent]:
        """Close open blocks on the exception path (infrastructure error).

        Called inside ``except`` before emitting ``RunErrorRenderEvent``.
        """
        yield from self._close_open_blocks("exception")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _close_open_blocks(self, reason: str) -> Iterator[RenderEvent]:
        """Close all open state-machine blocks and yield the corresponding *End events.

        Shared by ``close_truncated_stream`` and ``close_exception_path`` —
        the only difference between those two call-sites is the ``reason`` label
        used in log messages (F9, architect review — parallel-path-drift).

        Yields orphan ``ReasoningEndRenderEvent`` then ``TextEndRenderEvent``
        for whichever blocks are currently open, clearing their state. Also
        delegates to ``_synth_orphan_tool_ends`` so open tool calls are closed
        on the truncation/exception paths (symmetry with ``_handle_result``).
        """
        # ───── Slice 4 (#1101) orphan reasoning-close ─────
        open_reasoning = next(iter(self._sm_reasoning.open_blocks), None)
        if open_reasoning is not None:
            log.warning(
                "StreamProcessor: orphan ReasoningEnd synthesis (%s) message_id=…%s",
                reason,
                open_reasoning[-6:],
            )
            yield ReasoningEndRenderEvent(message_id=open_reasoning)
            self._sm_reasoning.close(open_reasoning)
        # ───── Slice 2 (#1099) v2 Text triplet — close open block ─────
        open_text = next(iter(self._sm_text.open_blocks), None)
        if open_text is not None:
            yield TextEndRenderEvent(message_id=open_text)
            self._sm_text.close(open_text)
        # ───── #1321 A4 — orphan ToolCallEnd symmetry on truncation/exception ─────
        yield from self._tool_handler.synth_orphan_tool_ends()


__all__ = ["StreamCloseHandler"]
