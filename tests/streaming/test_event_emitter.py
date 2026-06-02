"""Unit tests for EventEmitter[OutT] — Phase 5 streaming primitives (#1282).

Slice 4 tests. Covers emit_ok accumulation, flush clear semantics,
emit_terminal translator invocation, ordering invariant, type variance,
and translator exception propagation.
"""

from __future__ import annotations

import pytest

from factory.streaming.event_emitter import EventEmitter
from factory.transport import SanitizedError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_error(
    code: str = "TestError",
    message: str = "test error",
    retryable: bool = False,
) -> SanitizedError:
    return SanitizedError(code=code, message=message, retryable=retryable)


# ---------------------------------------------------------------------------
# emit_ok accumulation
# ---------------------------------------------------------------------------


class TestEmitOk:
    """emit_ok appends items to the pending queue in insertion order."""

    def test_emit_ok_accumulates_in_order(self) -> None:
        # Arrange
        emitter: EventEmitter[str] = EventEmitter(error_translator=lambda e: e.message)

        # Act
        emitter.emit_ok("a")
        emitter.emit_ok("b")
        result = list(emitter.flush())

        # Assert
        assert result == ["a", "b"]

    def test_emit_ok_returns_none(self) -> None:
        # Arrange
        emitter: EventEmitter[str] = EventEmitter(error_translator=lambda e: e.message)

        # Act / Assert
        assert emitter.emit_ok("item") is None


# ---------------------------------------------------------------------------
# flush clears pending
# ---------------------------------------------------------------------------


class TestFlush:
    """flush() yields pending items then clears the queue."""

    def test_flush_clears_pending_after_first_call(self) -> None:
        # Arrange
        emitter: EventEmitter[str] = EventEmitter(error_translator=lambda e: e.message)
        emitter.emit_ok("x")

        # Act
        list(emitter.flush())  # first flush consumes
        result = list(emitter.flush())  # second flush sees empty queue

        # Assert
        assert result == []

    def test_flush_empty_queue_returns_empty(self) -> None:
        # Arrange
        emitter: EventEmitter[str] = EventEmitter(error_translator=lambda e: e.message)

        # Act
        result = list(emitter.flush())

        # Assert
        assert result == []


# ---------------------------------------------------------------------------
# emit_terminal translator invocation
# ---------------------------------------------------------------------------


class TestEmitTerminal:
    """emit_terminal calls the translator with the exact SanitizedError instance."""

    def test_emit_terminal_calls_translator_with_exact_error(self) -> None:
        # Arrange
        received: list[SanitizedError] = []

        def capturing_translator(err: SanitizedError) -> str:
            received.append(err)
            return "translated"

        emitter: EventEmitter[str] = EventEmitter(error_translator=capturing_translator)
        err = make_error(code="E01", message="oops")

        # Act
        list(emitter.emit_terminal(err))

        # Assert — translator called exactly once with the same instance
        assert len(received) == 1
        assert received[0] is err

    def test_emit_terminal_yields_translator_return_value(self) -> None:
        # Arrange
        def code_translator(e: SanitizedError) -> str:
            return f"ERR:{e.code}"

        emitter: EventEmitter[str] = EventEmitter(error_translator=code_translator)
        err = make_error(code="E02")

        # Act
        result = list(emitter.emit_terminal(err))

        # Assert — single item matching the translator output
        assert result == ["ERR:E02"]

    def test_emit_terminal_yields_exactly_one_item(self) -> None:
        # Arrange
        emitter: EventEmitter[str] = EventEmitter(error_translator=lambda e: e.message)

        # Act
        result = list(emitter.emit_terminal(make_error()))

        # Assert
        assert len(result) == 1


# ---------------------------------------------------------------------------
# emit_terminal does NOT auto-flush pending
# ---------------------------------------------------------------------------


class TestEmitTerminalDoesNotFlushPending:
    """emit_terminal must not drain the pending queue."""

    def test_pending_items_remain_after_emit_terminal(self) -> None:
        # Arrange
        emitter: EventEmitter[str] = EventEmitter(error_translator=lambda e: e.message)
        emitter.emit_ok("pending-1")
        emitter.emit_ok("pending-2")

        # Act — terminal event emitted; pending items should survive
        list(emitter.emit_terminal(make_error()))

        # Assert — flush still returns the 2 pre-existing pending items
        remaining = list(emitter.flush())
        assert remaining == ["pending-1", "pending-2"]


# ---------------------------------------------------------------------------
# Ordering rule guard — caller flushes BEFORE emit_terminal
# ---------------------------------------------------------------------------


class TestOrderingInvariant:
    """Documents the canonical flush-before-terminal call order.

    The ordering invariant: callers MUST flush pending items before emitting
    the terminal event to preserve downstream ordering (e.g. TextEnd before
    RunErrorRenderEvent).  This test assembles the expected full sequence.
    """

    def test_flush_then_terminal_preserves_ordering(self) -> None:
        # Arrange
        def code_tr(e: SanitizedError) -> str:
            return f"ERR:{e.code}"

        emitter: EventEmitter[str] = EventEmitter(error_translator=code_tr)
        emitter.emit_ok("item-1")
        emitter.emit_ok("item-2")
        err = make_error(code="E99")

        # Act — canonical caller pattern
        result = [*emitter.flush(), *emitter.emit_terminal(err)]

        # Assert — pending items come before the terminal event
        assert result == ["item-1", "item-2", "ERR:E99"]

    def test_terminal_before_flush_breaks_ordering(self) -> None:
        """Negative guard: reversed order — terminal precedes pending items.

        This test fails when emit_terminal is deleted (the terminal output
        disappears and result shrinks), documenting the ordering dependency.
        """

        # Arrange
        def code_tr2(e: SanitizedError) -> str:
            return f"ERR:{e.code}"

        emitter: EventEmitter[str] = EventEmitter(error_translator=code_tr2)
        emitter.emit_ok("item-A")
        err = make_error(code="WRONG_ORDER")

        # Act — reversed (wrong) order for documentation purposes
        result = [*emitter.emit_terminal(err), *emitter.flush()]

        # Assert — terminal appears first (demonstrates the invariant violation);
        # full list shape so that deleting emit_ok's _pending.append breaks this test.
        assert result == ["ERR:WRONG_ORDER", "item-A"]


# ---------------------------------------------------------------------------
# Type-variance smoke
# ---------------------------------------------------------------------------


class TestTypeVariance:
    """EventEmitter is usable with RenderEvent and LlmEvent concrete types."""

    def test_render_event_type(self) -> None:
        # Arrange — use real RunErrorRenderEvent as OutT representative
        from factory.core.messaging.render_events import RunErrorRenderEvent

        def render_translator(err: SanitizedError) -> RunErrorRenderEvent:
            return RunErrorRenderEvent(run_id="run-1", message=err.message)

        emitter: EventEmitter[RunErrorRenderEvent] = EventEmitter(
            error_translator=render_translator
        )
        err = make_error(message="LLM timeout")

        # Act
        emitter.emit_ok(RunErrorRenderEvent(run_id="run-0", message="pre-terminal"))
        terminal = list(emitter.emit_terminal(err))
        pending = list(emitter.flush())

        # Assert
        assert isinstance(terminal[0], RunErrorRenderEvent)
        assert terminal[0].message == "LLM timeout"
        assert pending[0].run_id == "run-0"

    def test_llm_event_type(self) -> None:
        # Arrange — use real ResultLlmEvent as OutT representative
        from factory.core.messaging.events import ResultLlmEvent

        def llm_translator(err: SanitizedError) -> ResultLlmEvent:
            return ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                error_text=err.message,
            )

        emitter: EventEmitter[ResultLlmEvent] = EventEmitter(
            error_translator=llm_translator
        )
        err = make_error(message="stream broken")

        # Act
        terminal = list(emitter.emit_terminal(err))

        # Assert
        assert isinstance(terminal[0], ResultLlmEvent)
        assert terminal[0].is_error is True
        assert terminal[0].error_text == "stream broken"


# ---------------------------------------------------------------------------
# Translator raises
# ---------------------------------------------------------------------------


class TestTranslatorRaises:
    """If error_translator raises, emit_terminal propagates the exception."""

    def test_emit_terminal_propagates_translator_exception(self) -> None:
        # Arrange
        def raising_translator(err: SanitizedError) -> str:
            raise ValueError("translator failed")

        emitter: EventEmitter[str] = EventEmitter(error_translator=raising_translator)

        # Act / Assert — exception must not be swallowed
        with pytest.raises(ValueError, match="translator failed"):
            list(emitter.emit_terminal(make_error()))

    def test_pending_queue_unaffected_when_translator_raises(self) -> None:
        """Pending items survive a translator exception (queue not mutated)."""

        # Arrange
        def raising_translator(err: SanitizedError) -> str:
            raise RuntimeError("boom")

        emitter: EventEmitter[str] = EventEmitter(error_translator=raising_translator)
        emitter.emit_ok("safe-item")

        # Act — swallow the exception; pending queue must be intact
        with pytest.raises(RuntimeError):
            list(emitter.emit_terminal(make_error()))

        # Assert
        assert list(emitter.flush()) == ["safe-item"]
