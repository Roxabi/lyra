"""Tests for lyra.core.messaging.events — LlmEvent type system (S1).

Source: src/lyra/core/events.py
"""

from __future__ import annotations

import pytest

from lyra.core.messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
    ToolUseLlmEvent,
)

# ---------------------------------------------------------------------------
# TextLlmEvent
# ---------------------------------------------------------------------------


class TestTextLlmEvent:
    def test_construction(self) -> None:
        e = TextLlmEvent(text="hello")
        assert e.text == "hello"

    def test_repr(self) -> None:
        e = TextLlmEvent(text="hi")
        assert "hi" in repr(e)

    def test_frozen(self) -> None:
        e = TextLlmEvent(text="hello")
        with pytest.raises((AttributeError, TypeError)):
            setattr(e, "text", "world")

    def test_equality(self) -> None:
        assert TextLlmEvent(text="a") == TextLlmEvent(text="a")
        assert TextLlmEvent(text="a") != TextLlmEvent(text="b")

    def test_is_llm_event(self) -> None:
        e = TextLlmEvent(text="hi")
        assert isinstance(e, TextLlmEvent)


# ---------------------------------------------------------------------------
# ToolUseLlmEvent
# ---------------------------------------------------------------------------


class TestToolUseLlmEvent:
    def test_construction_with_input(self) -> None:
        e = ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={"path": "foo.py"})
        assert e.tool_name == "Edit"
        assert e.tool_id == "t1"
        assert e.input == {"path": "foo.py"}

    def test_default_empty_input(self) -> None:
        e = ToolUseLlmEvent(tool_name="Edit", tool_id="t1")
        assert e.input == {}

    def test_independent_default_inputs(self) -> None:
        """Two instances with default inputs share no state."""
        e1 = ToolUseLlmEvent(tool_name="Edit", tool_id="t1")
        e2 = ToolUseLlmEvent(tool_name="Bash", tool_id="t2")
        assert e1.input is not e2.input

    def test_frozen(self) -> None:
        e = ToolUseLlmEvent(tool_name="Edit", tool_id="t1")
        with pytest.raises((AttributeError, TypeError)):
            setattr(e, "tool_name", "Bash")

    def test_equality(self) -> None:
        a = ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={})
        b = ToolUseLlmEvent(tool_name="Edit", tool_id="t1", input={})
        assert a == b

    def test_is_not_hashable(self) -> None:
        """ToolUseLlmEvent is NOT hashable — input is a dict (mutable).

        Note the asymmetry: TextLlmEvent and ResultLlmEvent (scalar fields only)
        are hashable; ToolUseLlmEvent is not. Do not rely on hash() for LlmEvent
        union values without checking the concrete type first.
        """
        e = ToolUseLlmEvent(tool_name="Edit", tool_id="t1")
        with pytest.raises(TypeError):
            hash(e)


# ---------------------------------------------------------------------------
# ResultLlmEvent
# ---------------------------------------------------------------------------


class TestResultLlmEvent:
    def test_construction_with_cost(self) -> None:
        e = ResultLlmEvent(is_error=False, duration_ms=1200, cost_usd=0.012)
        assert e.is_error is False
        assert e.duration_ms == 1200
        assert e.cost_usd == pytest.approx(0.012)

    def test_cost_usd_defaults_to_none(self) -> None:
        """CLI driver never provides cost."""
        e = ResultLlmEvent(is_error=False, duration_ms=500)
        assert e.cost_usd is None

    def test_error_event(self) -> None:
        e = ResultLlmEvent(is_error=True, duration_ms=100)
        assert e.is_error is True

    def test_frozen(self) -> None:
        e = ResultLlmEvent(is_error=False, duration_ms=500)
        with pytest.raises((AttributeError, TypeError)):
            setattr(e, "is_error", True)

    def test_equality(self) -> None:
        a = ResultLlmEvent(is_error=False, duration_ms=500, cost_usd=None)
        b = ResultLlmEvent(is_error=False, duration_ms=500)
        assert a == b


# ---------------------------------------------------------------------------
# LlmEvent union
# ---------------------------------------------------------------------------


class TestLlmEventUnion:
    def test_text_is_union_member(self) -> None:
        e: LlmEvent = TextLlmEvent(text="x")
        assert isinstance(e, TextLlmEvent)

    def test_tool_is_union_member(self) -> None:
        e: LlmEvent = ToolUseLlmEvent(tool_name="Bash", tool_id="t1")
        assert isinstance(e, ToolUseLlmEvent)

    def test_result_is_union_member(self) -> None:
        e: LlmEvent = ResultLlmEvent(is_error=False, duration_ms=0)
        assert isinstance(e, ResultLlmEvent)

    def test_union_exported_from_module(self) -> None:
        """LlmEvent must be importable from lyra.core.messaging.events."""
        from lyra.core.messaging.events import LlmEvent as _LlmEvent  # noqa: F401

        assert _LlmEvent is LlmEvent

    def test_all_exports_complete(self) -> None:
        """Ensure __all__ matches the exact expected public API."""
        import lyra.core.messaging.events as _mod

        assert set(_mod.__all__) == {
            "LlmEvent",
            "ResultLlmEvent",
            "TextLlmEvent",
            "ToolUseLlmEvent",
        }
