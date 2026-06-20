"""ReasoningAccumulator — per-turn reasoning text accumulator + throttle logic.

Extracted from TelegramFormatter / DiscordFormatter (ADR-073 dedup, #1507).
Pure module — no platform imports, no I/O. Adapter-agnostic.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from factory.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from factory.outbound.throttle import STREAMING_EDIT_INTERVAL

# Truncation constants (shared by both platform formatters).
REASONING_MAX_LEN: int = 120
REASONING_TRUNC_LEN: int = 117


def _truncate_reasoning(text: str, *, max_len: int | None) -> str:
    """Truncate reasoning text when *max_len* is set; ``None`` disables truncation."""
    if max_len is None or len(text) <= max_len:
        return text
    trunc_len = max_len - (REASONING_MAX_LEN - REASONING_TRUNC_LEN)
    return text[:trunc_len] + "…"


@dataclass
class ReasoningAccumulator:
    """Accumulates streaming reasoning text for a single turn.

    Owns the truncate + throttle logic previously duplicated verbatim across
    TelegramFormatter and DiscordFormatter.

    ``clock`` is injectable for unit-test control (default: ``time.monotonic``).

    ``max_len`` controls truncation (default ``REASONING_MAX_LEN``). Pass
    ``None`` to stream the full accumulated text (e.g. Telegram rich
    ``<tg-thinking>`` blocks).

    Usage::

        self._reasoning = ReasoningAccumulator()
        # In edit_reasoning:
        text, should_edit = self._reasoning.process(event)
        if should_edit and text is not None:
            await <platform_edit>(trace_obj, self.dim_italic(text))
    """

    clock: Callable[[], float] = field(default_factory=lambda: time.monotonic)
    max_len: int | None = REASONING_MAX_LEN

    _accum: str = field(default="", init=False)
    _last_edit: float | None = field(default=None, init=False)

    def process(
        self,
        event: ReasoningStartRenderEvent
        | ReasoningDeltaRenderEvent
        | ReasoningEndRenderEvent,
    ) -> tuple[str | None, bool]:
        """Process one reasoning event, returning ``(text, should_edit)``.

        - ``Start``:  resets state; returns ``(None, False)`` (no edit on start).
        - ``Delta``:  accumulates delta; returns ``(truncated, True)`` when the
          throttle gate allows, ``(None, False)`` otherwise.
        - ``End``:    returns ``(truncated, True)`` if accum non-empty, else
          ``(None, False)``.  End is NOT throttle-gated — always flushes.
        """
        if isinstance(event, ReasoningStartRenderEvent):
            self._accum = ""
            self._last_edit = None
            return None, False

        if isinstance(event, ReasoningDeltaRenderEvent):
            self._accum += event.delta
            truncated = _truncate_reasoning(self._accum, max_len=self.max_len)
            now = self.clock()
            elapsed = None if self._last_edit is None else (now - self._last_edit)
            if elapsed is None or elapsed >= STREAMING_EDIT_INTERVAL:
                self._last_edit = now
                return truncated, True
            return None, False

        # ReasoningEndRenderEvent — flush unconditionally if non-empty
        if self._accum:
            truncated = _truncate_reasoning(self._accum, max_len=self.max_len)
            return truncated, True
        return None, False


__all__ = ["ReasoningAccumulator", "REASONING_MAX_LEN", "REASONING_TRUNC_LEN"]
