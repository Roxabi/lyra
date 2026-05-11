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

import pytest
from tools.audit_quality_debt import _SLUG_RE, _registry_status, scan

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
        "[importlinter]\nroot_packages = src\n\n[contract:example]\nignore_imports =\n"
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
        r["bucket"] == "POLICY"
        and r["tag"] == "boundary"
        and r["rule"] == "BLE001"
        and r["source"] == "noqa"
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
        r["bucket"] == "DEBT"
        and r.get("slug") == "foo"
        and r["rule"] == "BLE001"
        and r["source"] == "noqa"
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
    expected = {
        "noqa",
        "pyright-ignore",
        "type-ignore",
        "importlinter",
        "file-exemptions",
        "folder-exemptions",
    }
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
    for key in (
        "generated_at",
        "sources",
        "rows",
        "stale_references",
        "counts_by_rule_bucket_slug",
    ):
        assert key in report, f"missing key '{key}' in report"
    assert isinstance(report["sources"], list)
    assert set(report["sources"]) == {
        "noqa",
        "pyright-ignore",
        "type-ignore",
        "importlinter",
        "file-exemptions",
        "folder-exemptions",
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
    assert any(s["slug"] == "ghost" and s["reason"] == "missing" for s in stale), (
        f"expected stale slug=ghost reason=missing, got stale={stale}"
    )


def test_detects_stale_reference_drained_registry(tmp_path: Path) -> None:
    """DEBT:paid with status=drained -> stale_references reason=drained."""
    _make_debt_registry(tmp_path, "paid", "drained")
    _make_src_py(tmp_path, "src/lyra/x.py", "x = 1  # noqa: C901 -- DEBT:paid\n")
    out = tmp_path / "report.json"
    cp = _run(tmp_path, out)
    assert out.exists(), f"report not written; stderr={cp.stderr}"
    stale = _read_report(out)["stale_references"]
    assert any(s["slug"] == "paid" and s["reason"] == "drained" for s in stale), (
        f"expected stale slug=paid reason=drained, got stale={stale}"
    )


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


# ---------------------------------------------------------------------------
# T1 — path-traversal / malformed slug guard in _registry_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_slug",
    [
        "../escape",
        "../../etc",
        "foo/bar",
        "Foo",
        "-leading",
        "",
    ],
)
def test_registry_status_rejects_malformed_or_traversal_slugs(
    tmp_path: Path, bad_slug: str
) -> None:
    """_registry_status returns "open" for slugs that fail _SLUG_RE or escape debt dir.

    Negative-test: if the slug-validation guard (lines 160-169 of audit_quality_debt.py)
    were deleted, path-traversal slugs would reach resolve() and potentially
    read arbitrary files; this test would fail for traversal cases.
    """
    # Arrange — populate a real debt dir so path resolution has a real tree
    debt_dir = tmp_path / "artifacts" / "debt"
    debt_dir.mkdir(parents=True)

    # Act
    result = _registry_status(tmp_path, bad_slug)

    # Assert — guard must return "open" (safe no-op) for every bad input
    assert result == "open", (
        f"expected 'open' for malformed/traversal slug {bad_slug!r}, got {result!r}"
    )


# ---------------------------------------------------------------------------
# T2a — _SLUG_RE accept / reject (audit copy at tools/audit_quality_debt.py:30)
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
def test_audit_slug_regex(slug: str, expected: bool) -> None:
    """_SLUG_RE must accept lower-kebab identifiers and reject everything else.

    Negative-test: removing _SLUG_RE or widening its pattern would let
    malformed slugs pass the registry guard — this parametrized suite catches
    both directions of divergence.
    """
    # Act
    matched = bool(_SLUG_RE.fullmatch(slug))

    # Assert
    assert matched is expected, (
        f"_SLUG_RE.fullmatch({slug!r}) → {matched}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# T5 — _PY_SCAN_SKIP excludes tests/ and packages/ subtrees
# ---------------------------------------------------------------------------

_POLICY_LINE = "x = 1  # noqa: E501 -- POLICY:size\n"


def test_py_scan_skips_tests_and_packages(tmp_path: Path) -> None:
    """scan() includes src/ .py files and excludes tests/ and packages/ subtrees.

    Negative-test: if _PY_SCAN_SKIP lost "tests" or "packages", rows from
    those directories would appear and the assertions below would fail.
    """
    # Arrange — one file in each subtree, all with an identical POLICY line
    _make_src_py(tmp_path, "src/foo.py", _POLICY_LINE)
    _make_src_py(tmp_path, "tests/test_x.py", _POLICY_LINE)
    _make_src_py(tmp_path, "packages/y/z.py", _POLICY_LINE)

    # Act
    rows, _stale = scan(tmp_path)

    # Assert — only the src/ file should appear
    paths = {r["path"] for r in rows}
    assert any(p.startswith("src/") for p in paths), (
        f"expected src/foo.py in scan results, got paths={paths}"
    )
    assert not any(p.startswith("tests/") for p in paths), (
        f"tests/ should be excluded by _PY_SCAN_SKIP, got paths={paths}"
    )
    assert not any(p.startswith("packages/") for p in paths), (
        f"packages/ should be excluded by _PY_SCAN_SKIP, got paths={paths}"
    )
