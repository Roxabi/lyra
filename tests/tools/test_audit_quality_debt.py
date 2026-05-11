"""RED tests for tools/audit_quality_debt.py.

Tool does not exist yet — all tests FAIL. Correct RED state for this phase.

Contract:
  CLI: python tools/audit_quality_debt.py --root <dir> --out <file>
  Exit 0 iff: zero UNTAGGED rows in src/ AND zero stale_references.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TOOL = REPO / "tools" / "audit_quality_debt.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run(root: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )


def _make_src_py(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _make_importlinter(root: Path, lines: list[str]) -> Path:
    p = root / ".importlinter"
    body = (
        "[importlinter]\nroot_packages = src\n\n"
        "[contract:example]\nignore_imports =\n"
    )
    body += "".join(f"    {ln}\n" for ln in lines)
    p.write_text(body)
    return p


def _make_file_exemptions(root: Path, entries: list[str]) -> Path:
    p = root / "tools" / "file_exemptions.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(entries) + "\n")
    return p


def _make_folder_exemptions(root: Path, entries: list[str]) -> Path:
    p = root / "tools" / "folder_exemptions.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(entries) + "\n")
    return p


def _make_debt_registry(root: Path, slug: str, status: str) -> Path:
    p = root / "artifacts" / "debt" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nstatus: {status}\n---\n# {slug}\n")
    return p


def _read_report(out: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(out.read_text())


# ---------------------------------------------------------------------------
# test cases
# ---------------------------------------------------------------------------


def test_parses_noqa_policy_suffix(tmp_path: Path) -> None:
    """# noqa: BLE001 -- POLICY:boundary -> bucket=POLICY, tag=boundary."""
    _make_src_py(
        tmp_path, "src/lyra/cli/foo.py", "x = 1  # noqa: BLE001 -- POLICY:boundary\n"
    )
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    rows = _read_report(out)["rows"]
    assert any(
        r["bucket"] == "POLICY" and r["tag"] == "boundary"
        and r["rule"] == "BLE001" and r["source"] == "noqa"
        for r in rows
    ), f"expected POLICY:boundary row, got rows={rows}"


def test_parses_noqa_debt_suffix(tmp_path: Path) -> None:
    """# noqa: BLE001 -- DEBT:foo -> bucket=DEBT, slug=foo."""
    _make_debt_registry(tmp_path, "foo", "open")
    _make_src_py(tmp_path, "src/lyra/cli/foo.py", "x = 1  # noqa: BLE001 -- DEBT:foo\n")
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    rows = _read_report(out)["rows"]
    assert any(
        r["bucket"] == "DEBT" and r.get("slug") == "foo"
        and r["rule"] == "BLE001" and r["source"] == "noqa"
        for r in rows
    ), f"expected DEBT:foo row, got rows={rows}"


def test_parses_noqa_bare_untagged(tmp_path: Path) -> None:
    """Bare # noqa: BLE001 (no suffix) -> bucket=UNTAGGED."""
    _make_src_py(tmp_path, "src/lyra/x.py", "x = 1  # noqa: BLE001\n")
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    rows = _read_report(out)["rows"]
    assert any(
        r["bucket"] == "UNTAGGED" and r["rule"] == "BLE001" and r["source"] == "noqa"
        for r in rows
    ), f"expected UNTAGGED row, got rows={rows}"


def test_parses_all_six_sources(tmp_path: Path) -> None:
    """Fixture covers all 6 sources; report rows span all 6 source types."""
    _make_src_py(
        tmp_path,
        "src/lyra/a.py",
        (
            "x = 1  # noqa: E501 -- POLICY:layout\n"
            "y: int = 0  # pyright: ignore[reportUnknownVariableType]\n"
            "z: int = 0  # type: ignore[assignment]\n"
        ),
    )
    _make_importlinter(tmp_path, ["src.lyra.a -> src.lyra.b"])
    _make_file_exemptions(tmp_path, ["src/lyra/big_file.py"])
    _make_folder_exemptions(tmp_path, ["src/lyra/legacy"])
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    found = {r["source"] for r in _read_report(out)["rows"]}
    expected = {"noqa", "pyright-ignore", "type-ignore", "importlinter",
                "file-exemptions", "folder-exemptions"}
    missing = expected - found
    extra = found - expected
    assert expected == found, f"missing sources: {missing}; extra: {extra}"


def test_emits_report_schema(tmp_path: Path) -> None:
    """Report JSON has required top-level keys and rows have correct shape."""
    _make_debt_registry(tmp_path, "plr0913-wiring", "open")
    _make_src_py(
        tmp_path,
        "src/lyra/bootstrap/wire.py",
        "def f(): pass  # noqa: PLR0913 -- DEBT:plr0913-wiring\n",
    )
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    report = _read_report(out)
    for key in ("generated_at", "sources", "rows",
                "stale_references", "counts_by_rule_bucket_slug"):
        assert key in report, f"missing key '{key}' in report"
    assert isinstance(report["sources"], list)
    assert set(report["sources"]) == {
        "noqa", "pyright-ignore", "type-ignore",
        "importlinter", "file-exemptions", "folder-exemptions",
    }
    for row in report["rows"]:
        assert "source" in row, f"row missing 'source': {row}"
        assert "bucket" in row, f"row missing 'bucket': {row}"
        assert "path" in row, f"row missing 'path': {row}"
        if row["source"] == "noqa":
            assert "rule" in row, f"noqa row missing 'rule': {row}"
            assert "line" in row, f"noqa row missing 'line': {row}"


def test_detects_stale_reference_missing_registry(tmp_path: Path) -> None:
    """DEBT:ghost with no artifacts/debt/ghost.md -> stale_references reason=missing."""
    _make_src_py(tmp_path, "src/lyra/x.py", "x = 1  # noqa: C901 -- DEBT:ghost\n")
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    stale = _read_report(out)["stale_references"]
    assert any(
        s["slug"] == "ghost" and s["reason"] == "missing" for s in stale
    ), f"expected stale slug=ghost reason=missing, got stale={stale}"


def test_detects_stale_reference_drained_registry(tmp_path: Path) -> None:
    """DEBT:paid with status=drained -> stale_references reason=drained."""
    _make_debt_registry(tmp_path, "paid", "drained")
    _make_src_py(tmp_path, "src/lyra/x.py", "x = 1  # noqa: C901 -- DEBT:paid\n")
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    stale = _read_report(out)["stale_references"]
    assert any(
        s["slug"] == "paid" and s["reason"] == "drained" for s in stale
    ), f"expected stale slug=paid reason=drained, got stale={stale}"


def test_exit_code_zero_when_clean(tmp_path: Path) -> None:
    """Only POLICY tags, no UNTAGGED, no stale refs -> exit 0."""
    _make_src_py(
        tmp_path,
        "src/lyra/clean.py",
        "x = 1  # noqa: BLE001 -- POLICY:boundary\n",
    )
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    rc = cp.returncode
    assert rc == 0, f"expected exit 0; returncode={rc}, stderr={cp.stderr}"


def test_exit_code_nonzero_when_untagged(tmp_path: Path) -> None:
    """Any UNTAGGED row in src/ -> report written AND exit non-zero."""
    _make_src_py(tmp_path, "src/lyra/dirty.py", "x = 1  # noqa: E501\n")
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    assert cp.returncode != 0, (
        f"expected non-zero for untagged noqa; returncode={cp.returncode}"
    )
