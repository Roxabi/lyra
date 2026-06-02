"""Tests for tools/audit_quality_debt.py — warn-only semantics, no POLICY bucket.

Contract (GREEN target):
  - main() ALWAYS returns 0 regardless of untagged markers or stale refs.
  - POLICY bucket is never emitted (branch removed).
  - Warnings go to stderr for untagged sites and stale references.
  - Report JSON shape is stable: generated_at, sources, rows, stale_references,
    counts_by_rule_bucket_slug.
  - counts_by_rule_bucket_slug contains only DEBT and UNTAGGED bucket keys.

RED state: tests that assert exit-0 or no-POLICY will FAIL against the current
audit script (which exits 1 on untagged/stale and emits POLICY rows).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.audit_quality_debt import _SLUG_RE, _registry_status, main, scan

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_src_py(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _make_debt_registry(root: Path, slug: str, status: str) -> Path:
    p = root / "artifacts" / "debt" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nstatus: {status}\n---\n# {slug}\n")
    return p


def _read_report(out: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(out.read_text())


# ---------------------------------------------------------------------------
# T1 — exit-0 even with untagged markers
# ---------------------------------------------------------------------------


def test_audit_exits_zero_even_with_untagged_markers(tmp_path: Path) -> None:
    """main() returns 0 when src/ contains untagged noqa markers.

    RED: current audit returns 1 whenever untagged > 0.
    Negative-test: if exit-zero change is reverted, this fails.
    """
    # Arrange
    _make_src_py(tmp_path, "src/a.py", "x = 1  # noqa: BLE001\n")
    out = tmp_path / "report.json"

    # Act
    rc = main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
    assert rc == 0, f"expected exit 0 for untagged marker, got {rc}"
    report = _read_report(out)
    assert any(r["bucket"] == "UNTAGGED" for r in report["rows"]), (
        f"expected at least one UNTAGGED row, got rows={report['rows']}"
    )
    assert not any(r["bucket"] == "POLICY" for r in report["rows"]), (
        "POLICY bucket must not appear in output"
    )


# ---------------------------------------------------------------------------
# T2 — exit-0 with stale DEBT reference
# ---------------------------------------------------------------------------


def test_audit_exits_zero_with_stale_debt_reference(tmp_path: Path) -> None:
    """main() returns 0 when src/ has a DEBT: slug with no registry file.

    RED: current audit returns 1 whenever stale_src > 0.
    """
    # Arrange
    _make_src_py(tmp_path, "src/a.py", "x = 1  # noqa: BLE001 -- DEBT:does-not-exist\n")
    out = tmp_path / "report.json"

    # Act
    rc = main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
    assert rc == 0, f"expected exit 0 for stale DEBT reference, got {rc}"
    report = _read_report(out)
    stale = report["stale_references"]
    assert any(
        s["slug"] == "does-not-exist" and s["reason"] == "missing" for s in stale
    ), f"expected stale slug=does-not-exist reason=missing, got stale={stale}"


# ---------------------------------------------------------------------------
# T3 — exit-0 with clean repo (valid tagged marker + registry)
# ---------------------------------------------------------------------------


def test_audit_exits_zero_with_clean_repo(tmp_path: Path) -> None:
    """main() returns 0 for a fully-tagged repo with no stale refs.

    Also asserts: no UNTAGGED row for the tagged line, no stale_references.
    """
    # Arrange
    _make_debt_registry(tmp_path, "foo", "open")
    _make_src_py(tmp_path, "src/a.py", "x = 1  # noqa: BLE001 -- DEBT:foo\n")
    out = tmp_path / "report.json"

    # Act
    rc = main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
    assert rc == 0, f"expected exit 0 for clean repo, got {rc}"
    report = _read_report(out)
    assert not any(r["bucket"] == "POLICY" for r in report["rows"]), (
        "POLICY bucket must not appear in output"
    )
    assert not any(
        r["bucket"] == "UNTAGGED" and "src/a.py" in r.get("path", "")
        for r in report["rows"]
    ), "tagged line must not produce an UNTAGGED row"
    assert report["stale_references"] == [], (
        "expected empty stale_references for clean repo, "
        f"got {report['stale_references']}"
    )


# ---------------------------------------------------------------------------
# T4 — POLICY bucket is never emitted
# ---------------------------------------------------------------------------


def test_audit_never_emits_policy_bucket(tmp_path: Path) -> None:
    """Old POLICY suffix is not parsed — line treated as UNTAGGED, never as POLICY.

    RED: current _parse_suffix still returns ('POLICY', ...) for POLICY: lines.
    After GREEN: POLICY branch is gone; the line becomes UNTAGGED.
    """
    # Arrange — old-style POLICY annotation
    _make_src_py(tmp_path, "src/a.py", "x = 1  # noqa: BLE001 -- POLICY:boundary\n")
    out = tmp_path / "report.json"

    # Act
    main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
    report = _read_report(out)
    assert not any(r["bucket"] == "POLICY" for r in report["rows"]), (
        f"POLICY bucket must not appear; rows={report['rows']}"
    )


# ---------------------------------------------------------------------------
# T5 — stderr warnings for untagged markers
# ---------------------------------------------------------------------------


def test_audit_emits_warnings_on_stderr_for_untagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() writes a warning to stderr when src/ has untagged noqa markers.

    RED: current audit does NOT emit per-site stderr warnings (no WARN/untagged text).
    After GREEN: audit prints at least one line to stderr naming the file.
    """
    # Arrange
    _make_src_py(tmp_path, "src/a.py", "x = 1  # noqa: BLE001\n")
    out = tmp_path / "report.json"

    # Act
    rc = main(["--root", str(tmp_path), "--out", str(out)])
    captured = capsys.readouterr()

    # Assert
    assert rc == 0, f"expected exit 0, got {rc}"
    assert "src/a.py" in captured.err, (
        "expected 'src/a.py' in stderr for untagged marker, "
        f"got stderr={captured.err!r}"
    )


# ---------------------------------------------------------------------------
# T6 — stderr warnings for stale DEBT refs
# ---------------------------------------------------------------------------


def test_audit_emits_warnings_on_stderr_for_stale_refs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() writes a warning to stderr when a DEBT: slug has no registry file.

    RED: current audit does NOT emit per-site stderr stale warnings.
    After GREEN: audit prints to stderr containing the slug or "stale"/"missing".
    """
    # Arrange
    _make_src_py(tmp_path, "src/a.py", "x = 1  # noqa: C901 -- DEBT:ghost-slug\n")
    out = tmp_path / "report.json"

    # Act
    rc = main(["--root", str(tmp_path), "--out", str(out)])
    captured = capsys.readouterr()

    # Assert
    assert rc == 0, f"expected exit 0, got {rc}"
    err = captured.err
    assert any(kw in err for kw in ("ghost-slug", "stale", "missing")), (
        f"expected stale-ref warning in stderr, got stderr={err!r}"
    )


# ---------------------------------------------------------------------------
# T7 — stderr is quiet on a clean repo
# ---------------------------------------------------------------------------


def test_audit_silent_on_clean_repo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stderr carries no warnings when repo has no untagged or stale issues.

    After GREEN: warnings only emitted when there is something to warn about.
    """
    # Arrange — only a clean DEBT-tagged line with a valid registry
    _make_debt_registry(tmp_path, "bar", "open")
    _make_src_py(tmp_path, "src/b.py", "x = 1  # noqa: E501 -- DEBT:bar\n")
    out = tmp_path / "report.json"

    # Act
    rc = main(["--root", str(tmp_path), "--out", str(out)])
    captured = capsys.readouterr()

    # Assert
    assert rc == 0, f"expected exit 0, got {rc}"
    err = captured.err.upper()
    for kw in ("WARN", "UNTAGGED", "STALE"):
        assert kw not in err, (
            f"unexpected warning keyword {kw!r} in stderr for clean repo: "
            f"{captured.err!r}"
        )


# ---------------------------------------------------------------------------
# T8 — report JSON shape is stable
# ---------------------------------------------------------------------------


def test_audit_report_json_shape_unchanged(tmp_path: Path) -> None:
    """Top-level JSON keys survive the audit reshape."""
    # Arrange
    _make_debt_registry(tmp_path, "plr0913-wiring", "open")
    _make_src_py(
        tmp_path,
        "src/bootstrap/wire.py",
        "def f(): pass  # noqa: PLR0913 -- DEBT:plr0913-wiring\n",
    )
    out = tmp_path / "report.json"

    # Act
    main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
    report = _read_report(out)
    for key in (
        "generated_at",
        "sources",
        "rows",
        "stale_references",
        "counts_by_rule_bucket_slug",
    ):
        assert key in report, f"missing key {key!r} in report"
    assert isinstance(report["sources"], list)
    assert isinstance(report["rows"], list)
    assert isinstance(report["stale_references"], list)
    assert isinstance(report["counts_by_rule_bucket_slug"], dict)


# ---------------------------------------------------------------------------
# T9 — counts_by_rule_bucket_slug keys are only DEBT or UNTAGGED
# ---------------------------------------------------------------------------


def test_audit_counts_only_contain_debt_or_untagged(tmp_path: Path) -> None:
    """Bucket keys inside counts_by_rule_bucket_slug must be subset of {DEBT, UNTAGGED}.

    RED: current audit may emit POLICY bucket in counts when POLICY markers exist.
    After GREEN: POLICY branch removed; only DEBT and UNTAGGED survive.
    """
    # Arrange — mix of tagged (DEBT) and untagged noqa lines
    _make_debt_registry(tmp_path, "known", "open")
    _make_src_py(
        tmp_path,
        "src/mix.py",
        (
            "x = 1  # noqa: BLE001 -- DEBT:known\n"
            "y = 2  # noqa: E501\n"
            "z = 3  # noqa: C901 -- POLICY:old-style\n"
        ),
    )
    out = tmp_path / "report.json"

    # Act
    main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
    report = _read_report(out)
    allowed = {"DEBT", "UNTAGGED"}
    for rule, bucket_map in report["counts_by_rule_bucket_slug"].items():
        illegal = set(bucket_map.keys()) - allowed
        assert not illegal, (
            f"rule={rule!r} has illegal bucket keys {illegal} "
            "in counts_by_rule_bucket_slug"
        )


# ---------------------------------------------------------------------------
# T10 — packages/ and tests/ subtrees are excluded from warnings
# ---------------------------------------------------------------------------


def test_audit_no_warnings_in_packages_or_tests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Untagged markers under packages/ and tests/ produce no stderr warnings.

    _PY_SCAN_SKIP filters those trees; they must not appear in stderr output.
    Negative-test: if _PY_SCAN_SKIP loses "tests" or "packages", those paths
    would appear in stderr and this test would fail.
    """
    # Arrange — untagged markers in skipped trees only
    _make_src_py(tmp_path, "packages/foo/x.py", "x = 1  # noqa: BLE001\n")
    _make_src_py(tmp_path, "tests/y.py", "x = 1  # noqa: E501\n")
    out = tmp_path / "report.json"

    # Act
    rc = main(["--root", str(tmp_path), "--out", str(out)])
    captured = capsys.readouterr()

    # Assert
    assert rc == 0, f"expected exit 0, got {rc}"
    assert "packages/foo/x.py" not in captured.err, (
        f"packages/ path must not appear in stderr: {captured.err!r}"
    )
    assert "tests/y.py" not in captured.err, (
        f"tests/ path must not appear in stderr: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# Retained: slug regex and registry-status guard (unchanged by this PR)
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
    """_registry_status returns 'open' for slugs that fail _SLUG_RE or escape debt dir.

    Negative-test: if the slug-validation guard were deleted, path-traversal slugs
    would reach resolve() and potentially read arbitrary files.
    """
    # Arrange
    debt_dir = tmp_path / "artifacts" / "debt"
    debt_dir.mkdir(parents=True)

    # Act
    result = _registry_status(tmp_path, bad_slug)

    # Assert
    assert result == "open", (
        f"expected 'open' for malformed/traversal slug {bad_slug!r}, got {result!r}"
    )


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
    """_SLUG_RE accepts lower-kebab identifiers and rejects everything else.

    Negative-test: widening _SLUG_RE would let malformed slugs bypass the guard.
    """
    # Act
    matched = bool(_SLUG_RE.fullmatch(slug))

    # Assert
    assert matched is expected, (
        f"_SLUG_RE.fullmatch({slug!r}) → {matched}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# Retained: scan() excludes tests/ and packages/
# ---------------------------------------------------------------------------


def test_py_scan_skips_tests_and_packages(tmp_path: Path) -> None:
    """scan() includes src/ .py files and excludes tests/ and packages/ subtrees.

    Negative-test: if _PY_SCAN_SKIP lost "tests" or "packages", rows from those
    directories would appear and the assertions below would fail.
    """
    # Arrange
    _make_src_py(tmp_path, "src/foo.py", "x = 1  # noqa: E501\n")
    _make_src_py(tmp_path, "tests/test_x.py", "x = 1  # noqa: E501\n")
    _make_src_py(tmp_path, "packages/y/z.py", "x = 1  # noqa: E501\n")

    # Act
    rows, _ = scan(tmp_path)

    # Assert
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


# ---------------------------------------------------------------------------
# Retained: DEBT parsing and stale-reference detection
# ---------------------------------------------------------------------------


def test_parses_noqa_debt_suffix(tmp_path: Path) -> None:
    """# noqa: BLE001 -- DEBT:foo -> bucket=DEBT, slug=foo."""
    # Arrange
    _make_debt_registry(tmp_path, "foo", "open")
    _make_src_py(
        tmp_path, "src/factory/cli/foo.py", "x = 1  # noqa: BLE001 -- DEBT:foo\n"
    )
    out = tmp_path / "report.json"

    # Act
    main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
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
    # Arrange
    _make_src_py(tmp_path, "src/factory/x.py", "x = 1  # noqa: BLE001\n")
    out = tmp_path / "report.json"

    # Act
    main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
    rows = _read_report(out)["rows"]
    assert any(
        r["bucket"] == "UNTAGGED" and r["rule"] == "BLE001" and r["source"] == "noqa"
        for r in rows
    ), f"expected UNTAGGED row, got rows={rows}"


def test_detects_stale_reference_missing_registry(tmp_path: Path) -> None:
    """DEBT:ghost with no artifacts/debt/ghost.md -> stale_references reason=missing."""
    # Arrange
    _make_src_py(tmp_path, "src/factory/x.py", "x = 1  # noqa: C901 -- DEBT:ghost\n")
    out = tmp_path / "report.json"

    # Act
    main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
    stale = _read_report(out)["stale_references"]
    assert any(s["slug"] == "ghost" and s["reason"] == "missing" for s in stale), (
        f"expected stale slug=ghost reason=missing, got stale={stale}"
    )


def test_detects_stale_reference_drained_registry(tmp_path: Path) -> None:
    """DEBT:paid with status=drained -> stale_references reason=drained."""
    # Arrange
    _make_debt_registry(tmp_path, "paid", "drained")
    _make_src_py(tmp_path, "src/factory/x.py", "x = 1  # noqa: C901 -- DEBT:paid\n")
    out = tmp_path / "report.json"

    # Act
    main(["--root", str(tmp_path), "--out", str(out)])

    # Assert
    stale = _read_report(out)["stale_references"]
    assert any(s["slug"] == "paid" and s["reason"] == "drained" for s in stale), (
        f"expected stale slug=paid reason=drained, got stale={stale}"
    )
