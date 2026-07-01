"""Tests for tools/check_doc_semantic_drift.py — Phase C semantic doc gate."""

from __future__ import annotations

import re
import sys
from io import StringIO
from pathlib import Path

from tools.check_doc_semantic_drift import RULES, main


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


def test_stale_container_count_message_has_no_hardcoded_count() -> None:
    """The stale_container_count fix-message must not itself hardcode a count.

    Regression for the self-referential staleness the 2026-06-30 audit caught: the
    message said "nine Quadlet containers" while the live count kept climbing, so the
    gate's own guidance became the stale fact it exists to correct. Point at the
    generated SSoT (`CURRENT.generated.md` / `deploy/quadlet.toml`) instead.
    """
    rule = next(r for r in RULES if r.rule_id == "stale_container_count")
    assert not re.search(
        r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen)\s+(?:Quadlet\s+)?containers?\b",
        rule.message,
        re.IGNORECASE,
    ), f"fix-message must not hardcode a container count: {rule.message!r}"
