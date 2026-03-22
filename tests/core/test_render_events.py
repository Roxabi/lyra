"""Tests for lyra.core.render_events — RenderEvent type system (S1).

Source: src/lyra/core/render_events.py
"""

from __future__ import annotations

import pytest

from lyra.core.render_events import (
    FileEditSummary,
    RenderEvent,
    SilentCounts,
    TextRenderEvent,
    ToolSummaryRenderEvent,
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
            sc.reads = 5  # type: ignore[misc]

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
            s.path = "b.py"  # type: ignore[misc]

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
        s.edits.append("fn_b")  # type: ignore[union-attr]  # mutate original
        assert snap.edits == ["fn_a"]


# ---------------------------------------------------------------------------
# TextRenderEvent
# ---------------------------------------------------------------------------


class TestTextRenderEvent:
    def test_construction(self) -> None:
        e = TextRenderEvent(text="hello", is_final=True)
        assert e.text == "hello"
        assert e.is_final is True

    def test_is_final_is_required(self) -> None:
        """is_final has no default — callers must be explicit about finality."""
        with pytest.raises(TypeError):
            TextRenderEvent(text="partial")  # type: ignore[call-arg]

    def test_frozen(self) -> None:
        e = TextRenderEvent(text="x", is_final=True)
        with pytest.raises((AttributeError, TypeError)):
            e.text = "y"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = TextRenderEvent(text="hi", is_final=True)
        b = TextRenderEvent(text="hi", is_final=True)
        assert a == b
        assert a != TextRenderEvent(text="hi", is_final=False)

    def test_is_render_event(self) -> None:
        """TextRenderEvent is a member of the RenderEvent union discriminator."""
        e = TextRenderEvent(text="hi", is_final=True)
        assert isinstance(e, (TextRenderEvent, ToolSummaryRenderEvent))


# ---------------------------------------------------------------------------
# ToolSummaryRenderEvent
# ---------------------------------------------------------------------------


class TestToolSummaryRenderEvent:
    def test_defaults(self) -> None:
        e = ToolSummaryRenderEvent()
        assert e.files == {}
        assert e.bash_commands == []
        assert e.web_fetches == []
        assert e.agent_calls == []
        assert e.silent_counts == SilentCounts()
        assert e.is_complete is False

    def test_construction_full(self) -> None:
        sc = SilentCounts(reads=2)
        summary = FileEditSummary(path="a.py", edits=["fn"], count=1)
        e = ToolSummaryRenderEvent(
            files={"a.py": summary},
            bash_commands=["uv run pytest"],
            web_fetches=["example.com"],
            agent_calls=["sub-agent"],
            silent_counts=sc,
            is_complete=True,
        )
        assert e.files == {"a.py": summary}
        assert e.bash_commands == ["uv run pytest"]
        assert e.web_fetches == ["example.com"]
        assert e.agent_calls == ["sub-agent"]
        assert e.silent_counts == sc
        assert e.is_complete is True

    def test_independent_defaults(self) -> None:
        a = ToolSummaryRenderEvent()
        b = ToolSummaryRenderEvent()
        assert a.files is not b.files
        assert a.bash_commands is not b.bash_commands
        assert a.web_fetches is not b.web_fetches
        assert a.agent_calls is not b.agent_calls
        assert a.silent_counts is not b.silent_counts

    def test_frozen(self) -> None:
        e = ToolSummaryRenderEvent()
        with pytest.raises((AttributeError, TypeError)):
            e.is_complete = True  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ToolSummaryRenderEvent(bash_commands=["ls"])
        b = ToolSummaryRenderEvent(bash_commands=["ls"])
        assert a == b
        assert a != ToolSummaryRenderEvent(bash_commands=["pwd"])

    def test_is_not_hashable(self) -> None:
        """ToolSummaryRenderEvent is NOT hashable — contains mutable containers."""
        e = ToolSummaryRenderEvent()
        with pytest.raises(TypeError):
            hash(e)


# ---------------------------------------------------------------------------
# RenderEvent union
# ---------------------------------------------------------------------------


class TestRenderEventUnion:
    def test_text_is_union_member(self) -> None:
        e: RenderEvent = TextRenderEvent(text="hi", is_final=True)
        assert isinstance(e, TextRenderEvent)

    def test_tool_summary_is_union_member(self) -> None:
        e: RenderEvent = ToolSummaryRenderEvent()
        assert isinstance(e, ToolSummaryRenderEvent)

    def test_union_exported_from_module(self) -> None:
        from lyra.core.render_events import RenderEvent as _RenderEvent  # noqa: F401

        assert _RenderEvent is RenderEvent

    def test_all_exports_complete(self) -> None:
        """Ensure __all__ matches the exact expected public API."""
        import lyra.core.render_events as _mod

        assert set(_mod.__all__) == {
            "FileEditSummary",
            "RenderEvent",
            "SilentCounts",
            "TextRenderEvent",
            "ToolSummaryRenderEvent",
        }


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
            _root / "src" / "lyra" / "llm" / "events.py",
            _root / "src" / "lyra" / "core" / "render_events.py",
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
