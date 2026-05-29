"""Unit tests for ReasoningAccumulator (#1507).

Covers:
- Start resets state and returns (None, False)
- Delta accumulates text
- Delta truncates >120 chars to [:117] + "…"
- Delta throttle: suppresses too-soon edits, allows after interval
- End flushes non-empty accum unconditionally (no throttle gate)
- End no-ops on empty accum
"""

from __future__ import annotations

from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from lyra.outbound._reasoning_accum import (
    REASONING_MAX_LEN,
    REASONING_TRUNC_LEN,
    ReasoningAccumulator,
)
from lyra.outbound.throttle import STREAMING_EDIT_INTERVAL

_MSG_ID = "test-msg-1"


def _start() -> ReasoningStartRenderEvent:
    return ReasoningStartRenderEvent(message_id=_MSG_ID)


def _delta(text: str) -> ReasoningDeltaRenderEvent:
    return ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta=text)


def _end() -> ReasoningEndRenderEvent:
    return ReasoningEndRenderEvent(message_id=_MSG_ID)


class TestReasoningAccumulatorStart:
    """Start event resets state."""

    def test_start_returns_none_false(self) -> None:
        accum = ReasoningAccumulator()
        result = accum.process(_start())
        assert result == (None, False)

    def test_start_resets_after_prior_delta(self) -> None:
        """A second Start discards previous accum."""
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)

        accum.process(_start())
        accum.process(_delta("first block"))

        # Second Start — resets
        result = accum.process(_start())
        assert result == (None, False)

        # Delta after second Start accumulates fresh
        clock_val = STREAMING_EDIT_INTERVAL + 1.0
        text, should_edit = accum.process(_delta("fresh"))
        assert should_edit is True
        assert text == "fresh"


class TestReasoningAccumulatorDelta:
    """Delta accumulation, truncation, and throttle."""

    def test_delta_accumulates(self) -> None:
        """Multiple deltas concatenate."""
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)
        accum.process(_start())

        accum.process(_delta("hello "))
        clock_val = STREAMING_EDIT_INTERVAL + 1.0
        text2, should_edit2 = accum.process(_delta("world"))

        assert should_edit2 is True
        assert text2 == "hello world"

    def test_delta_short_text_not_truncated(self) -> None:
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)
        accum.process(_start())
        text, should_edit = accum.process(_delta("a" * REASONING_MAX_LEN))
        assert should_edit is True
        assert text == "a" * REASONING_MAX_LEN
        assert "…" not in (text or "")

    def test_delta_long_text_truncated(self) -> None:
        """Text > 120 chars is truncated to 117 + ellipsis."""
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)
        accum.process(_start())
        long_text = "x" * 200
        text, should_edit = accum.process(_delta(long_text))
        assert should_edit is True
        assert text is not None
        assert len(text) == REASONING_TRUNC_LEN + 1  # 117 chars + "…" (1 char)
        assert text.endswith("…")
        assert text[:REASONING_TRUNC_LEN] == "x" * REASONING_TRUNC_LEN

    def test_delta_throttle_suppresses_too_soon(self) -> None:
        """A Delta that arrives before STREAMING_EDIT_INTERVAL returns (None, False)."""
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)
        accum.process(_start())

        # First Delta at t=0 — allowed
        _, ok0 = accum.process(_delta("a"))
        assert ok0 is True

        # Second Delta at t=0 (same instant, below interval) — suppressed
        suppressed = accum.process(_delta("b"))
        assert suppressed == (None, False)

    def test_delta_throttle_allows_after_interval(self) -> None:
        """A Delta after STREAMING_EDIT_INTERVAL elapses is allowed."""
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)
        accum.process(_start())

        # First Delta at t=0
        accum.process(_delta("a"))

        # Second Delta at t = interval (exactly on boundary — >=, so allowed)
        clock_val = STREAMING_EDIT_INTERVAL
        text, should_edit = accum.process(_delta("b"))
        assert should_edit is True
        assert text == "ab"

    def test_delta_first_edit_always_allowed(self) -> None:
        """The first Delta after Start is always allowed (last_edit is None)."""
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)
        accum.process(_start())
        text, should_edit = accum.process(_delta("first"))
        assert should_edit is True
        assert text == "first"


class TestReasoningAccumulatorEnd:
    """End event flushes unconditionally."""

    def test_end_flushes_nonempty_accum(self) -> None:
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)
        accum.process(_start())

        # Consume the first allowed edit slot
        accum.process(_delta("hello"))

        # End must flush unconditionally (no throttle check)
        text, should_edit = accum.process(_end())
        assert should_edit is True
        assert text == "hello"

    def test_end_noop_on_empty_accum(self) -> None:
        accum = ReasoningAccumulator()
        accum.process(_start())
        text, should_edit = accum.process(_end())
        assert should_edit is False
        assert text is None

    def test_end_truncates_long_accum(self) -> None:
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)
        accum.process(_start())
        long_text = "y" * 200
        accum.process(_delta(long_text))

        text, should_edit = accum.process(_end())
        assert should_edit is True
        assert text is not None
        assert text.endswith("…")
        assert len(text) == REASONING_TRUNC_LEN + 1

    def test_end_not_throttle_gated(self) -> None:
        """End fires even if the last edit was very recent (no throttle gate on End)."""
        clock_val = 0.0
        accum = ReasoningAccumulator(clock=lambda: clock_val)
        accum.process(_start())

        # First delta at t=0 — sets last_edit=0
        accum.process(_delta("data"))

        # End at t=0 (same instant) — must still return should_edit=True
        text, should_edit = accum.process(_end())
        assert should_edit is True
        assert text == "data"
