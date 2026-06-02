"""Text and reasoning sub-handlers for the StreamProcessor pipeline.

Extracted from ``stream_processor.py`` (Slice 3, #1282 / #1590) so that
text-handling logic lives in its own unit and can be composed back into
``StreamProcessor`` via delegation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING
from uuid import uuid4

from factory.core.messaging.events import TextLlmEvent, ThinkingLlmEvent
from factory.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningStartRenderEvent,
    RenderEvent,
    TextDeltaRenderEvent,
    TextStartRenderEvent,
)
from factory.streaming.state_machine import StateMachine

if TYPE_CHECKING:
    from factory.core.processors.stream_close import StreamCloseHandler

log = logging.getLogger(__name__)


def _mint_text_block_id() -> str:
    """Per-block message id for the v2 Text triplet (Slice 2, #1099).

    Format: ``"text-<12-char-hex>"``. Mirrors the ``synthetic-<uuid4>`` shape
    used by ``run_id`` minting; distinguishable via prefix. Module-level helper.
    """
    return f"text-{uuid4().hex[:12]}"


def _mint_reasoning_block_id() -> str:
    """Per-block message id for a reasoning block (Slice 4, #1101).

    Format: ``"reasoning-<12-char-hex>"``. Mirrors ``_mint_text_block_id``
    — same uuid4 approach, distinguishable via prefix.
    """
    return f"reasoning-{uuid4().hex[:12]}"


class StreamTextHandler:
    """Handle ``TextLlmEvent`` and ``ThinkingLlmEvent`` in a StreamProcessor.

    One instance per turn. Owns the text and reasoning state machines and
    the text-accumulator buffers that feed downstream formatting.
    """

    def __init__(
        self,
        sm_text: StateMachine[str, str],
        sm_reasoning: StateMachine[str, str],
        show_intermediate: bool,
        close_handler: "StreamCloseHandler | None",
    ) -> None:
        self._sm_text = sm_text
        self._sm_reasoning = sm_reasoning
        self._show_intermediate = show_intermediate
        self._close_handler = close_handler

        # --- text accumulators ---
        self._pending_text: str = ""
        self._total_text: str = ""  # full accumulated text for final emit

    # ------------------------------------------------------------------
    # Public handlers
    # ------------------------------------------------------------------

    def clear_pending_text(self) -> None:
        """Reset the pending text accumulator (called on tool-use transitions)."""
        self._pending_text = ""

    def handle_text(self, event: TextLlmEvent) -> Iterator[RenderEvent]:
        """Emit TextStart/TextDelta; manage open text block via _sm_text."""
        if self._close_handler is not None:
            yield from self._close_handler.close_reasoning_if_open()
        self._pending_text += event.text
        self._total_text += event.text
        open_text = next(iter(self._sm_text.open_blocks), None)
        if open_text is None:
            block_id = _mint_text_block_id()
            self._sm_text.open(block_id, "text")
            yield TextStartRenderEvent(message_id=block_id)
            open_text = block_id
        yield TextDeltaRenderEvent(message_id=open_text, delta=event.text)

    def handle_thinking(self, event: ThinkingLlmEvent) -> Iterator[RenderEvent]:
        """Emit ReasoningStart/Delta; manage open reasoning block via _sm_reasoning."""
        # SC-6 (spec v2 lines 65, 260): drop the chunk entirely
        # when show_intermediate=False — no Reasoning* produced.
        if not self._show_intermediate:
            return
        open_reasoning = next(iter(self._sm_reasoning.open_blocks), None)
        if open_reasoning is None:
            block_id = _mint_reasoning_block_id()
            self._sm_reasoning.open(block_id, "reasoning")
            log.debug("reasoning block opened message_id=%s", block_id)
            yield ReasoningStartRenderEvent(message_id=block_id)
            open_reasoning = block_id
        yield ReasoningDeltaRenderEvent(message_id=open_reasoning, delta=event.text)


__all__ = [
    "StreamTextHandler",
    "_mint_text_block_id",
    "_mint_reasoning_block_id",
]
