"""Tests for lyra.core.messaging.render_events — RenderEvent type system (S1).

Source: src/lyra/core/messaging/render_events.py
"""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import FrozenInstanceError

import pytest

from lyra.core.messaging.render_events import (
    FileEditSummary,
    RenderEvent,
    SilentCounts,
)

# ---------------------------------------------------------------------------
# SilentCounts
# ---------------------------------------------------------------------------


class TestSilentCounts:
    def test_defaults_are_zero(self) -> None:
        sc = SilentCounts()
        assert sc.reads == 0
        assert sc.greps == 0
        assert sc.globs == 0

    def test_construction(self) -> None:
        sc = SilentCounts(reads=3, greps=1, globs=2)
        assert sc.reads == 3
        assert sc.greps == 1
        assert sc.globs == 2

    def test_frozen(self) -> None:
        sc = SilentCounts(reads=1)
        with pytest.raises((AttributeError, TypeError)):
            setattr(sc, "reads", 5)

    def test_equality(self) -> None:
        assert SilentCounts(reads=1) == SilentCounts(reads=1)
        assert SilentCounts(reads=1) != SilentCounts(reads=2)

    def test_is_hashable(self) -> None:
        """SilentCounts is hashable — all fields are scalars."""
        sc = SilentCounts(reads=1, greps=2, globs=3)
        assert hash(sc) == hash(SilentCounts(reads=1, greps=2, globs=3))
        assert {sc}  # can be placed in a set


# ---------------------------------------------------------------------------
# FileEditSummary
# ---------------------------------------------------------------------------


class TestFileEditSummary:
    def test_construction_required_field_only(self) -> None:
        s = FileEditSummary(path="src/foo.py")
        assert s.path == "src/foo.py"
        assert s.edits == []
        assert s.count == 0

    def test_construction_full(self) -> None:
        s = FileEditSummary(path="src/foo.py", edits=["edit_fn"], count=1)
        assert s.edits == ["edit_fn"]
        assert s.count == 1

    def test_independent_default_edits(self) -> None:
        a = FileEditSummary(path="a.py")
        b = FileEditSummary(path="b.py")
        assert a.edits is not b.edits

    def test_frozen(self) -> None:
        s = FileEditSummary(path="a.py")
        with pytest.raises((AttributeError, TypeError)):
            setattr(s, "path", "b.py")

    def test_equality(self) -> None:
        a = FileEditSummary(path="x.py", edits=["f"], count=1)
        b = FileEditSummary(path="x.py", edits=["f"], count=1)
        assert a == b

    def test_is_not_hashable(self) -> None:
        """FileEditSummary is NOT hashable — edits is a list (mutable)."""
        s = FileEditSummary(path="x.py")
        with pytest.raises(TypeError):
            hash(s)

    def test_snapshot_returns_equal_copy(self) -> None:
        """snapshot() produces an equal but independent copy."""
        s = FileEditSummary(path="x.py", edits=["fn_a", "fn_b"], count=2)
        snap = s.snapshot()
        assert snap == s
        assert snap.edits is not s.edits  # independent list

    def test_snapshot_independence(self) -> None:
        """Mutating the original's edits after snapshot() does not affect the snap."""
        s = FileEditSummary(path="x.py", edits=["fn_a"], count=1)
        snap = s.snapshot()
        assert s.edits is not None
        s.edits.append("fn_b")  # mutate original
        assert snap.edits == ["fn_a"]


# ---------------------------------------------------------------------------
# RenderEvent union
# ---------------------------------------------------------------------------


class TestRenderEventUnion:
    def test_union_exported_from_module(self) -> None:
        from lyra.core.messaging.render_events import (
            RenderEvent as _RenderEvent,  # noqa: F401
        )

        assert _RenderEvent is RenderEvent

    def test_all_exports_complete(self) -> None:
        """Ensure __all__ matches the public API derived from the RenderEvent union.

        Dynamic: sourced from typing.get_args(RenderEvent) so this test adapts
        automatically when union members are added or removed (N6 / #1177).
        The test will stay green through Slice 3's v1 removal without manual
        maintenance.
        """
        import re

        import lyra.core.messaging.render_events as _mod

        def _class_to_schema_const(name: str) -> str:
            """Convert CamelCase class name to SCHEMA_VERSION_UPPER_SNAKE_CASE."""
            return "SCHEMA_VERSION_" + re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()

        union_members = typing.get_args(_mod.RenderEvent)
        # Each union member type name must be exported
        expected_type_names = {cls.__name__ for cls in union_members}
        # Each union member has a matching SCHEMA_VERSION_* constant
        expected_schema_consts = {
            _class_to_schema_const(n) for n in expected_type_names
        }
        # Static non-union exports (support types + the union alias itself)
        static_exports = {"FileEditSummary", "SilentCounts", "RenderEvent"}

        expected = expected_type_names | expected_schema_consts | static_exports

        assert set(_mod.__all__) == expected


# ---------------------------------------------------------------------------
# ToolCall* lifecycle events (Slice 3 of #1096)
# ---------------------------------------------------------------------------


class TestToolCallEvents:
    def test_tool_call_start_frozen_and_default_schema(self) -> None:
        from lyra.core.messaging.render_events import (
            SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT,
            ToolCallStartRenderEvent,
        )

        e = ToolCallStartRenderEvent(tool_call_id="toolu_AB", tool_name="Read")
        assert e.tool_call_id == "toolu_AB"
        assert e.tool_name == "Read"
        assert e.schema_version == SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT == 1
        with pytest.raises(FrozenInstanceError):
            e.tool_call_id = "x"  # type: ignore[misc]

    def test_tool_call_args_frozen_and_default_schema(self) -> None:
        from lyra.core.messaging.render_events import (
            SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT,
            ToolCallArgsRenderEvent,
        )

        e = ToolCallArgsRenderEvent(tool_call_id="toolu_AB", delta='{"foo":')
        assert e.delta == '{"foo":'
        assert e.schema_version == SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT == 1
        with pytest.raises(FrozenInstanceError):
            e.delta = "y"  # type: ignore[misc]

    def test_tool_call_end_frozen_and_default_schema(self) -> None:
        from lyra.core.messaging.render_events import (
            SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT,
            ToolCallEndRenderEvent,
        )

        e = ToolCallEndRenderEvent(tool_call_id="toolu_AB")
        assert e.schema_version == SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT == 1
        with pytest.raises(FrozenInstanceError):
            e.tool_call_id = "x"  # type: ignore[misc]

    def test_tool_call_result_frozen_and_default_schema(self) -> None:
        from lyra.core.messaging.render_events import (
            SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT,
            ToolCallResultRenderEvent,
        )

        e = ToolCallResultRenderEvent(tool_call_id="toolu_AB", content="ok")
        assert e.is_error is False
        assert e.schema_version == SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT == 1
        e_err = ToolCallResultRenderEvent(
            tool_call_id="toolu_AB", content="boom", is_error=True
        )
        assert e_err.is_error is True
        with pytest.raises(FrozenInstanceError):
            e.content = "y"  # type: ignore[misc]

    def test_tool_call_events_in_union(self) -> None:
        from lyra.core.messaging.render_events import (
            ToolCallArgsRenderEvent,
            ToolCallEndRenderEvent,
            ToolCallResultRenderEvent,
            ToolCallStartRenderEvent,
        )

        events: list[RenderEvent] = [
            ToolCallStartRenderEvent(tool_call_id="x", tool_name="Read"),
            ToolCallArgsRenderEvent(tool_call_id="x", delta="{}"),
            ToolCallEndRenderEvent(tool_call_id="x"),
            ToolCallResultRenderEvent(tool_call_id="x", content="ok"),
        ]
        assert len(events) == 4


# ---------------------------------------------------------------------------
# Reasoning* lifecycle events (Slice 4 of #1096 — issue #1101)
# ---------------------------------------------------------------------------


class TestReasoningEvents:
    def test_reasoning_events_frozen(self) -> None:
        from lyra.core.messaging.render_events import (
            SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT,
            SCHEMA_VERSION_REASONING_END_RENDER_EVENT,
            SCHEMA_VERSION_REASONING_START_RENDER_EVENT,
            ReasoningDeltaRenderEvent,
            ReasoningEndRenderEvent,
            ReasoningStartRenderEvent,
        )

        # ReasoningStartRenderEvent
        assert dataclasses.is_dataclass(ReasoningStartRenderEvent)
        e_start = ReasoningStartRenderEvent(message_id="m1")
        assert e_start.message_id == "m1"
        assert (
            e_start.schema_version == SCHEMA_VERSION_REASONING_START_RENDER_EVENT == 1
        )
        with pytest.raises(FrozenInstanceError):
            e_start.message_id = "x"  # type: ignore[misc]

        # ReasoningDeltaRenderEvent
        assert dataclasses.is_dataclass(ReasoningDeltaRenderEvent)
        e_delta = ReasoningDeltaRenderEvent(message_id="m1", delta="x")
        assert e_delta.delta == "x"
        assert (
            e_delta.schema_version == SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT == 1
        )
        with pytest.raises(FrozenInstanceError):
            e_delta.delta = "y"  # type: ignore[misc]

        # ReasoningEndRenderEvent
        assert dataclasses.is_dataclass(ReasoningEndRenderEvent)
        e_end = ReasoningEndRenderEvent(message_id="m1")
        assert e_end.message_id == "m1"
        assert e_end.schema_version == SCHEMA_VERSION_REASONING_END_RENDER_EVENT == 1
        with pytest.raises(FrozenInstanceError):
            e_end.message_id = "x"  # type: ignore[misc]

    def test_reasoning_events_in_union(self) -> None:
        from lyra.core.messaging.render_events import (
            ReasoningDeltaRenderEvent,
            ReasoningEndRenderEvent,
            ReasoningStartRenderEvent,
        )

        union_args = typing.get_args(RenderEvent)
        assert ReasoningStartRenderEvent in union_args
        assert ReasoningDeltaRenderEvent in union_args
        assert ReasoningEndRenderEvent in union_args

    def test_reasoning_constants_exported(self) -> None:
        import lyra.core.messaging.render_events as _mod

        assert "SCHEMA_VERSION_REASONING_START_RENDER_EVENT" in _mod.__all__
        assert "SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT" in _mod.__all__
        assert "SCHEMA_VERSION_REASONING_END_RENDER_EVENT" in _mod.__all__
        assert _mod.SCHEMA_VERSION_REASONING_START_RENDER_EVENT == 1
        assert _mod.SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT == 1
        assert _mod.SCHEMA_VERSION_REASONING_END_RENDER_EVENT == 1


# ---------------------------------------------------------------------------
# Hexagonal boundary — no framework imports
# ---------------------------------------------------------------------------


class TestHexagonalBoundary:
    def test_grep_no_framework_imports(self) -> None:
        """Programmatic check: neither module contains forbidden imports."""
        import ast
        from pathlib import Path

        forbidden = {"aiogram", "discord", "anthropic"}
        # Anchor to this file's location so the test works from any cwd.
        _root = Path(__file__).resolve().parent.parent.parent
        paths = [
            _root / "src" / "lyra" / "core" / "messaging" / "events.py",
            _root / "src" / "lyra" / "core" / "messaging" / "render_events.py",
        ]
        for path in paths:
            assert path.exists(), f"Source not found: {path}"
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [node.module or ""]
                        if isinstance(node, ast.ImportFrom)
                        else [alias.name for alias in node.names]
                    )
                    for name in names:
                        for f in forbidden:
                            assert not (name or "").startswith(f), (
                                f"{path}: forbidden import '{name}'"
                            )
