"""Tests for tools/check_doc_drift.py — doc-drift gate.

Contract:
  - Dead backtick ref not in baseline → exit 1, printed to stdout.
  - Live ref (real src/ path or symbol) → exit 0.
  - Historical annotation on same line → exempt, exit 0.
  - Baselined ref → exempt, exit 0.
  - <!-- drift-ignore --> on same line → exempt, exit 0.
"""

from __future__ import annotations

from pathlib import Path

from tools.check_doc_drift import main

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_doc(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _make_src_class(root: Path, symbol: str) -> None:
    """Create a minimal src file defining the given symbol so it resolves."""
    p = root / "src" / "lyra" / "_test_symbols.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = p.read_text() if p.exists() else ""
    p.write_text(existing + f"\nclass {symbol}: ...\n")


# ---------------------------------------------------------------------------
# T1 — dead ref not in baseline → exit 1
# ---------------------------------------------------------------------------


def test_dead_ref_not_in_baseline_exits_1(tmp_path: Path) -> None:
    _make_doc(
        tmp_path,
        "docs/architecture/test.md",
        "Some doc referencing `ZzzGhostClass` which does not exist.\n",
    )
    rc = main(["--root", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# T2 — live src/ path ref → exit 0
# ---------------------------------------------------------------------------


def test_live_src_path_ref_passes(tmp_path: Path) -> None:
    # Create the real file
    real = tmp_path / "src" / "lyra" / "core" / "hub.py"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("# hub\n")
    _make_doc(
        tmp_path,
        "docs/architecture/test.md",
        "See `src/lyra/core/hub.py` for details.\n",
    )
    rc = main(["--root", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# T3 — live symbol (class defined in src/) → exit 0
# ---------------------------------------------------------------------------


def test_live_symbol_passes(tmp_path: Path) -> None:
    _make_src_class(tmp_path, "ClaudeCliDriver")
    _make_doc(
        tmp_path,
        "docs/architecture/test.md",
        "Use `ClaudeCliDriver` for single-process wiring.\n",
    )
    rc = main(["--root", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# T4 — historical annotation on same line → exempt → exit 0
# ---------------------------------------------------------------------------


def test_historical_annotation_exempts(tmp_path: Path) -> None:
    _make_doc(
        tmp_path,
        "docs/architecture/test.md",
        "The `ZzzGhostClass` driver was deleted #1281 and is no longer in the tree.\n",
    )
    rc = main(["--root", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# T5 — baselined ref → exempt → exit 0
# ---------------------------------------------------------------------------


def test_baselined_ref_is_exempt(tmp_path: Path) -> None:
    _make_doc(
        tmp_path,
        "docs/architecture/test.md",
        "See `ZzzGhostClass` for details.\n",
    )
    baseline = tmp_path / "tools" / "doc_drift_baseline.txt"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(
        "# baseline\ndocs/architecture/test.md::ZzzGhostClass\n", encoding="utf-8"
    )
    rc = main(["--root", str(tmp_path), "--baseline", str(baseline)])
    assert rc == 0


# ---------------------------------------------------------------------------
# T6 — drift-ignore comment → exempt → exit 0
# ---------------------------------------------------------------------------


def test_drift_ignore_comment_exempts(tmp_path: Path) -> None:
    _make_doc(
        tmp_path,
        "docs/architecture/test.md",
        "See `ZzzGhostClass` for context. <!-- drift-ignore -->\n",
    )
    rc = main(["--root", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# T7 — --update-baseline writes file and exits 0
# ---------------------------------------------------------------------------


def test_update_baseline_writes_and_exits_0(tmp_path: Path) -> None:
    _make_doc(
        tmp_path,
        "docs/architecture/test.md",
        "See `ZzzGhostClass` here.\n",
    )
    baseline = tmp_path / "tools" / "doc_drift_baseline.txt"
    rc = main(
        ["--root", str(tmp_path), "--baseline", str(baseline), "--update-baseline"]
    )
    assert rc == 0
    assert baseline.exists()
    content = baseline.read_text()
    assert "docs/architecture/test.md::ZzzGhostClass" in content


# ---------------------------------------------------------------------------
# T8 — ADR archive is not scanned (historical records) → exit 0
# ---------------------------------------------------------------------------


def test_adr_archive_is_not_scanned(tmp_path: Path) -> None:
    # A dead ref inside docs/architecture/adr/ must NOT trip the gate: ADRs are
    # immutable decision records citing symbols as-of-writing (ADR-080).
    _make_doc(
        tmp_path,
        "docs/architecture/adr/099-old-decision.mdx",
        "This ADR referenced `ZzzGhostClass` at decision time.\n",
    )
    rc = main(["--root", str(tmp_path)])
    assert rc == 0
