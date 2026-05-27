"""Tests for workspace command path-constraint helpers (#1434)."""
from __future__ import annotations

from pathlib import Path

from lyra.core.commands.workspace_commands import _constrain_to_base


class TestConstrainToBase:
    def test_allows_path_inside_base(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        assert _constrain_to_base(sub, tmp_path) == sub.resolve()

    def test_rejects_traversal_via_dotdot(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()
        escape = base / ".." / "outside"
        assert _constrain_to_base(escape, base) is None

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = base / "link"
        link.symlink_to(outside)
        assert _constrain_to_base(link, base) is None

    def test_skips_constraint_when_base_none(self, tmp_path: Path) -> None:
        assert _constrain_to_base(tmp_path, None) == tmp_path

    def test_rejects_absolute_outside_base(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        assert _constrain_to_base(outside, base) is None
