"""Tests for tools/check_doc_semantic_drift.py — Phase C semantic doc gate."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

from tools.check_doc_semantic_drift import main


def _pyproject(root: Path) -> None:
    root.joinpath("pyproject.toml").write_text(
        '[project]\nlicense = { text = "AGPL-3.0-or-later" }\n',
        encoding="utf-8",
    )


def _run(root: Path, args: list[str] | None = None) -> tuple[int, str]:
    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = main(["--root", str(root)] + (args or []))
    finally:
        sys.stdout = old
    return rc, buf.getvalue()


def test_clean_operational_doc_passes(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text(
        "# Lyra\n\n[![License: AGPL](badge)](LICENSE)\n\n"
        "## License\n\nAGPL-3.0-or-later\n",
        encoding="utf-8",
    )
    doc = tmp_path / "docs" / "DEPLOYMENT.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("Nine containers. Use `make factory status`.\n", encoding="utf-8")
    rc, out = _run(tmp_path)
    assert rc == 0
    assert "OK" in out


def test_make_lyra_fails(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# Lyra\nAGPL\n", encoding="utf-8")
    doc = tmp_path / "docs" / "COMMANDS.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("Run `make lyra reload`.\n", encoding="utf-8")
    rc, out = _run(tmp_path)
    assert rc == 1
    assert "make_lyra" in out


def test_history_exempt(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# Lyra\nAGPL\n", encoding="utf-8")
    doc = tmp_path / "docs" / "history" / "old.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("make lyra stop\n", encoding="utf-8")
    rc, _ = _run(tmp_path)
    assert rc == 0


def test_semantic_ignore_exempt(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# Lyra\nAGPL\n", encoding="utf-8")
    doc = tmp_path / "docs" / "ops" / "note.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("make lyra reload <!-- semantic-ignore -->\n", encoding="utf-8")
    rc, _ = _run(tmp_path)
    assert rc == 0


def test_readme_mit_fails(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text(
        "[![License: MIT](badge)](LICENSE)\n\n## License\n\nMIT\n",
        encoding="utf-8",
    )
    rc, out = _run(tmp_path)
    assert rc == 1
    assert "readme" in out.lower()