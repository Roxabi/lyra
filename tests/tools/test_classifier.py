"""Tests for tools/classify_quality_debt.py.

Contract:
  CLI: python tools/classify_quality_debt.py [--report PATH] [--dry-run|--apply]
  Default report: artifacts/quality-debt-report.json (resolved from cwd)

Dry-run stdout format (TSV per UNTAGGED entry):
  <path>\\t<line>\\t<rule>\\t<suggestion>\\t<fix_class>

Summary line on stdout:
  CLASSIFIED: <n> / UNTAGGED: <m> / NEEDS_REVIEW: <k> / RATIO: <r>
  RATIO = classified / (m - k), skipped when (m - k) == 0.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools.classify_quality_debt import _SLUG_RE, _safe_repo_path

REPO = Path(__file__).resolve().parent.parent.parent
TOOL = REPO / "tools" / "classify_quality_debt.py"

_INDEX_HEADER = (
    "# Quality-Debt Registry — INDEX\n\n"
    "| Slug | Status | Rules | Sites | Drain slice | Created |\n"
    "|------|--------|-------|-------|-------------|------|\n"
    "<!-- rows inserted here -->\n"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _row(path: str, rule: str, line: int = 1) -> dict[str, Any]:
    return {"source": "noqa", "path": path, "bucket": "UNTAGGED",
            "rule": rule, "line": line}


def _write_report(p: Path, rows: list[dict[str, Any]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "generated_at": "2026-05-11T16:00:00Z", "sources": ["noqa"],
        "rows": rows, "stale_references": [], "counts_by_rule_bucket_slug": {},
    }))


def _src(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _run(
    root: Path, report: Path, mode: str = "--dry-run"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), mode, "--report", str(report)],
        capture_output=True, text=True, check=False, cwd=str(root),
    )


def _tsv(stdout: str, skip_header: bool = True) -> list[dict[str, str]]:
    """Parse TSV lines: path\\tline\\trule\\tsuggestion\\tfix_class.

    skip_header=True (default) filters the header row so callers only
    see real data rows.
    """
    out = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 5:
            if skip_header and parts[0] == "path":
                continue
            out.append({"path": parts[0], "line": parts[1], "rule": parts[2],
                        "suggestion": parts[3], "fix_class": parts[4]})
    return out


def _debt_dir(root: Path) -> None:
    d = root / "artifacts" / "debt"
    d.mkdir(parents=True, exist_ok=True)
    (d / "INDEX.md").write_text(_INDEX_HEADER)


# ---------------------------------------------------------------------------
# test cases
# ---------------------------------------------------------------------------


def test_ble001_at_cli_toplevel_suggests_policy_boundary(tmp_path: Path) -> None:
    """BLE001 in cli_*.py -> POLICY:boundary, fix_class=easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def handle():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert rows, f"no TSV rows; stdout:\n{cp.stdout}"
    assert any(r["rule"] == "BLE001" and r["suggestion"] == "POLICY:boundary"
               for r in rows), f"got rows={rows}"


def test_b008_typer_option_suggests_policy_typer_default(tmp_path: Path) -> None:
    """B008 on line with typer.Option( -> POLICY:typer-default, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/cli/args.py"
    _src(tmp_path, sp,
         "import typer\n\ndef cmd(x: int = typer.Option(0)):  # noqa: B008\n    pass\n")
    _write_report(rpt, [_row(sp, "B008", line=3)])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "B008" and r["suggestion"] == "POLICY:typer-default"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_plr0913_wiring_path_suggests_policy_wiring(tmp_path: Path) -> None:
    """PLR0913 in bootstrap/* -> POLICY:wiring, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/wire.py"
    _src(tmp_path, sp, "def build(a, b, c, d, e, f, g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0913" and r["suggestion"] == "POLICY:wiring"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_c901_migration_function_suggests_policy_migration_sequence(
    tmp_path: Path,
) -> None:
    """C901 on def _atomic_*(...): -> POLICY:migration-sequence, medium."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/db/migrations.py"
    _src(tmp_path, sp,
         "def _atomic_table_copy(src, dst, conn):  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "C901"
               and r["suggestion"] == "POLICY:migration-sequence"
               and r["fix_class"] == "medium" for r in rows), f"got rows={rows}"


def test_f401_in_init_flagged_needs_review(tmp_path: Path) -> None:
    """F401 in __init__.py -> needs_review (excluded from 80% denominator)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/x/__init__.py"
    _src(tmp_path, sp, "from lyra.x.impl import Foo  # noqa: F401\n")
    _write_report(rpt, [_row(sp, "F401")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "F401" and r["fix_class"] == "needs_review"
               for r in rows), f"got rows={rows}"


def test_classification_ratio_above_80pct(tmp_path: Path) -> None:
    """8 classifiable + 2 needs_review -> RATIO >= 0.80 in stdout summary."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    rows: list[dict[str, Any]] = []
    for i in range(2):
        p = f"src/lyra/cli/cli_cmd{i}.py"
        _src(tmp_path, p, "def h():  # noqa: BLE001\n    pass\n")
        rows.append(_row(p, "BLE001"))
    for i in range(2):
        p = f"src/lyra/cli/opt{i}.py"
        _src(tmp_path, p, "def cmd(x=typer.Option(0)):  # noqa: B008\n    pass\n")
        rows.append(_row(p, "B008"))
    for i in range(2):
        p = f"src/lyra/bootstrap/wire{i}.py"
        _src(tmp_path, p, "def w(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
        rows.append(_row(p, "PLR0913"))
    for i in range(2):
        p = f"src/lyra/db/mig{i}.py"
        _src(tmp_path, p, f"def _atomic_step{i}(x):  # noqa: C901\n    pass\n")
        rows.append(_row(p, "C901"))
    for i in range(2):
        p = f"src/lyra/mod{i}/__init__.py"
        _src(tmp_path, p, "from lyra.x import Foo  # noqa: F401\n")
        rows.append(_row(p, "F401"))
    _write_report(rpt, rows)

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    ratio_line = next(
        (ln for ln in cp.stdout.splitlines() if "RATIO" in ln.upper()), None
    )
    assert ratio_line is not None, f"no RATIO line; stdout:\n{cp.stdout}"
    m = re.search(r"RATIO[:\s]+([0-9.]+)", ratio_line, re.IGNORECASE)
    assert m is not None, f"could not parse ratio from: {ratio_line}"
    assert float(m.group(1)) >= 0.80, f"ratio too low: {ratio_line}"


def test_apply_writes_inline_policy_suffix(tmp_path: Path) -> None:
    """--apply writes the inline POLICY:<tag> suffix on UNTAGGED rows."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def h():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt, "--apply")

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    edited = (tmp_path / sp).read_text()
    assert "POLICY:boundary" in edited, (
        f"expected POLICY:boundary in source; got:\n{edited}"
    )


def test_apply_creates_registry_for_debt_suggestion(tmp_path: Path) -> None:
    """--apply creates artifacts/debt/<slug>.md on DEBT:<slug> suggestion."""
    import pytest

    pytest.skip(
        "Current classifier heuristics emit POLICY only; DEBT-emitting patterns "
        "tracked in P2a #1163."
    )


def test_apply_produces_drain_queue_with_cap(tmp_path: Path) -> None:
    """--apply with 55 easy rows -> drain queue exists with <=30 easy entries."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    rows: list[dict[str, Any]] = []
    for i in range(55):
        p = f"src/lyra/bootstrap/wire{i}.py"
        _src(tmp_path, p, "def w(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
        rows.append(_row(p, "PLR0913"))
    _write_report(rpt, rows)

    cp = _run(tmp_path, rpt, "--apply")

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    queue_path = tmp_path / "artifacts" / "quality-debt-drain-queue.json"
    assert queue_path.exists(), (
        f"drain queue missing; stdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"
    )
    queue = json.loads(queue_path.read_text())
    assert isinstance(queue, list) and len(queue) > 0, "drain queue empty"
    easy = [e for e in queue if e.get("fix_class") == "easy"]
    assert len(easy) <= 30, f"easy cap exceeded: {len(easy)} > 30"


# ---------------------------------------------------------------------------
# T2b — _SLUG_RE accept/reject (classifier copy at line 74)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug, expected",
    [
        ("valid-slug-1", True),
        ("a", True),
        ("Foo", False),
        ("a/b", False),
        ("-leading", False),
        ("", False),
    ],
)
def test_classifier_slug_regex(slug: str, expected: bool) -> None:
    """_SLUG_RE accepts valid lowercase-alphanumeric slugs and rejects all others."""
    # Arrange: slug under test, expected match result
    # Act
    match = _SLUG_RE.fullmatch(slug)
    # Assert
    assert bool(match) == expected, (
        f"_SLUG_RE.fullmatch({slug!r}) returned {match!r}; expected match={expected}"
    )


# ---------------------------------------------------------------------------
# T3 — Batched-edits dedup: single write per file for >=2 POLICY tags
# ---------------------------------------------------------------------------


def test_apply_writes_once_for_multiple_policy_tags(tmp_path: Path) -> None:
    """--apply on a file with 2 POLICY-tagged rows edits both lines in one pass.

    Verify that both lines carry the inline suffix (proving batched write
    succeeded) and that no 'skipped duplicate edit' appears in stderr
    (proving the dedup guard did not fire on distinct line numbers).
    """
    # Arrange
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/cli/cli_cmd.py"
    # Two BLE001 violations on separate lines — both classifiable as POLICY:boundary
    _src(
        tmp_path,
        sp,
        "def handle_a():  # noqa: BLE001\n"
        "    pass\n"
        "\n"
        "def handle_b():  # noqa: BLE001\n"
        "    pass\n",
    )
    _write_report(
        rpt,
        [_row(sp, "BLE001", line=1), _row(sp, "BLE001", line=4)],
    )

    # Act
    cp = _run(tmp_path, rpt, "--apply")

    # Assert: process succeeded
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"

    edited = (tmp_path / sp).read_text()

    # Both lines must carry the suffix — proves the batched write reached all edits
    lines = edited.splitlines()
    assert "POLICY:boundary" in lines[0], (
        f"line 1 missing POLICY:boundary; got: {lines[0]!r}"
    )
    assert "POLICY:boundary" in lines[3], (
        f"line 4 missing POLICY:boundary; got: {lines[3]!r}"
    )

    # No duplicate-edit warning — distinct line numbers must not trigger dedup guard
    assert "skipped duplicate edit" not in cp.stderr, (
        f"unexpected dedup warning in stderr:\n{cp.stderr}"
    )


# ---------------------------------------------------------------------------
# T8 — reason field propagation through drain-queue JSON output
# ---------------------------------------------------------------------------


def test_drain_queue_carries_reason_field(tmp_path: Path) -> None:
    """drain-queue entries carry a 'reason' field set by _classify_row.

    PLR0913 on a wiring path -> status=classified, reason='matched'.
    The drain-queue JSON entry for that row must have reason='matched'.
    """
    # Arrange
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/bootstrap/wire.py"
    _src(tmp_path, sp, "def build(a, b, c, d, e, f, g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    # Act
    cp = _run(tmp_path, rpt, "--apply")

    # Assert: process succeeded and queue exists
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    queue_path = tmp_path / "artifacts" / "quality-debt-drain-queue.json"
    assert queue_path.exists(), (
        f"drain queue missing; stdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"
    )

    queue = json.loads(queue_path.read_text())
    assert isinstance(queue, list) and len(queue) > 0, "drain queue is empty"

    entry = queue[0]
    # 'reason' key must be present and non-empty
    assert "reason" in entry, f"'reason' key missing from drain-queue entry: {entry}"
    assert entry["reason"], f"'reason' is empty in drain-queue entry: {entry}"
    # PLR0913 on wiring path -> matched heuristic
    assert entry["reason"] == "matched", (
        f"expected reason='matched', got {entry['reason']!r}"
    )


# ---------------------------------------------------------------------------
# T9 — _safe_repo_path containment + apply-path no-write
# ---------------------------------------------------------------------------


def test_safe_repo_path_blocks_traversal(tmp_path: Path) -> None:
    """_safe_repo_path returns None for paths that escape the repo root."""
    # Arrange: tmp_path is the root

    # Act + Assert: path traversal attempts return None
    assert _safe_repo_path(tmp_path, "../escape") is None, (
        "_safe_repo_path should return None for '../escape'"
    )
    assert _safe_repo_path(tmp_path, "/abs/outside") is None, (
        "_safe_repo_path should return None for '/abs/outside'"
    )

    # Act + Assert: valid relative path inside root returns a Path
    result = _safe_repo_path(tmp_path, "valid/sub.py")
    assert result is not None, (
        "_safe_repo_path should return a Path for 'valid/sub.py'"
    )
    assert result.is_relative_to(tmp_path), (
        f"returned path {result} is not inside root {tmp_path}"
    )


def test_apply_does_not_write_outside_root(tmp_path: Path) -> None:
    """--apply with a traversal path in the report skips that row silently.

    The guard at lines 400 and 434 of classify_quality_debt.py must
    short-circuit without writing any file outside tmp_path and without
    raising an unhandled exception (rc must be 0).
    """
    # Arrange: sibling directory that the traversal path would escape to
    sibling = tmp_path.parent / f"escape_target_{tmp_path.name}"
    sibling.mkdir(exist_ok=True)
    escape_file = sibling / "should_not_be_written.py"

    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)

    # Build a path string that resolves outside tmp_path
    # e.g. "../escape_target_<name>/should_not_be_written.py"
    relative_escape = f"../escape_target_{tmp_path.name}/should_not_be_written.py"

    # Also plant a legitimate row so the apply path has something to do
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def h():  # noqa: BLE001\n    pass\n")
    _write_report(
        rpt,
        [
            _row(relative_escape, "BLE001", line=1),
            _row(sp, "BLE001", line=1),
        ],
    )

    # Act
    cp = _run(tmp_path, rpt, "--apply")

    # Assert: no unhandled exception (rc=0)
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"

    # Assert: the escape file was never created
    assert not escape_file.exists(), (
        f"apply wrote outside root: {escape_file} was created"
    )
