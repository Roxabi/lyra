"""Tests for tools/archive_artifacts_wave.py — the artifacts archive wave (#2218).

The census (which issues are closed) is injected via ``--closed-issues-file`` so
these tests never touch the network; they exercise the candidate discovery,
link-rewrite, and 0-broken-links acceptance bar offline.
"""

from __future__ import annotations

import json
import re
import sys
from io import StringIO
from pathlib import Path

from tools.archive_artifacts_wave import (
    Candidate,
    _broken_refs_in_text,
    _extract_issue,
    build_plan,
    iter_candidates,
    main,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(root: Path) -> None:
    """A minimal repo: two specs (one closed, one open) + inbound references."""
    _write(
        root / "artifacts" / "specs" / "1234-foo-spec.mdx",
        "---\nissue: 1234\n---\n# foo\n",
    )
    _write(
        root / "artifacts" / "specs" / "5678-bar-spec.mdx",
        "---\nissue: 5678\n---\n# bar\n",
    )
    # A spec whose issue lives only in frontmatter (no numeric basename prefix).
    _write(
        root / "artifacts" / "goal" / "quux-goal.md",
        "---\nissue: 1234\n---\n# quux\n",
    )
    # Repo-root style reference (a domain page) + ADR-style relative reference.
    _write(
        root / "docs" / "architecture" / "obs.md",
        "See `artifacts/specs/1234-foo-spec.mdx` for the delta.\n",
    )
    _write(
        root / "docs" / "architecture" / "adr" / "091.mdx",
        "> truth → [spec](../../../artifacts/specs/1234-foo-spec.mdx)\n",
    )
    # A machine consumer (gate script) citing the same path.
    _write(
        root / "tools" / "some_gate.sh",
        "grep x artifacts/goal/quux-goal.md\n",
    )


def _closed_file(root: Path, numbers: list[int]) -> Path:
    p = root / "closed.json"
    p.write_text(json.dumps(numbers), encoding="utf-8")
    return p


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = main(argv)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return rc, out.getvalue(), err.getvalue()


def test_extract_issue_from_basename_and_frontmatter(tmp_path: Path) -> None:
    _tree(tmp_path)
    assert _extract_issue(tmp_path / "artifacts/specs/1234-foo-spec.mdx") == 1234
    assert _extract_issue(tmp_path / "artifacts/goal/quux-goal.md") == 1234


def test_iter_candidates_covers_swept_categories(tmp_path: Path) -> None:
    _tree(tmp_path)
    cands: list[Candidate] = iter_candidates(tmp_path)
    names = {c.basename for c in cands}
    assert "1234-foo-spec.mdx" in names
    assert "5678-bar-spec.mdx" in names
    assert "quux-goal.md" in names


def test_dry_run_plans_only_closed_issue_deltas(tmp_path: Path) -> None:
    _tree(tmp_path)
    closed = _closed_file(tmp_path, [1234])
    rc, out, _ = _run(
        [
            "--root",
            str(tmp_path),
            "--month",
            "2026-07",
            "--closed-issues-file",
            str(closed),
        ]
    )
    assert rc == 0
    assert "0 broken links" in out
    # The closed-issue deltas are scheduled; the open one is not.
    assert "1234-foo-spec.mdx" in out
    assert "quux-goal.md" in out
    assert "5678-bar-spec.mdx" not in out
    # Nothing moved on a dry run.
    assert (tmp_path / "artifacts/specs/1234-foo-spec.mdx").exists()


def test_apply_rewrites_then_moves_zero_broken(tmp_path: Path) -> None:
    _tree(tmp_path)
    closed = _closed_file(tmp_path, [1234])
    rc, out, err = _run(
        [
            "--root",
            str(tmp_path),
            "--month",
            "2026-07",
            "--closed-issues-file",
            str(closed),
            "--apply",
            "--no-git",
        ]
    )
    assert rc == 0, err
    assert "applied: 2 moved" in out
    # Files relocated under the archive bucket.
    assert (tmp_path / "artifacts/archive/2026-07/1234-foo-spec.mdx").exists()
    assert (tmp_path / "artifacts/archive/2026-07/quux-goal.md").exists()
    assert not (tmp_path / "artifacts/specs/1234-foo-spec.mdx").exists()
    # Repo-root ref rewritten.
    obs = (tmp_path / "docs/architecture/obs.md").read_text(encoding="utf-8")
    assert "artifacts/archive/2026-07/1234-foo-spec.mdx" in obs
    assert "artifacts/specs/1234-foo-spec.mdx" not in obs
    # ADR-style relative ref: the ../../../ prefix still resolves to repo root.
    adr = (tmp_path / "docs/architecture/adr/091.mdx").read_text(encoding="utf-8")
    assert "../../../artifacts/archive/2026-07/1234-foo-spec.mdx" in adr
    # Machine-consumer (gate script) ref rewritten too.
    gate = (tmp_path / "tools/some_gate.sh").read_text(encoding="utf-8")
    assert "artifacts/archive/2026-07/quux-goal.md" in gate
    # The open-issue delta stayed put.
    assert (tmp_path / "artifacts/specs/5678-bar-spec.mdx").exists()


def test_no_candidates_when_nothing_closed(tmp_path: Path) -> None:
    _tree(tmp_path)
    closed = _closed_file(tmp_path, [])
    rc, out, _ = _run(["--root", str(tmp_path), "--closed-issues-file", str(closed)])
    assert rc == 0
    assert "no closed-issue deltas to archive" in out


def test_residual_broken_is_empty_by_construction(tmp_path: Path) -> None:
    _tree(tmp_path)
    plan = build_plan(tmp_path, "2026-07", {1234})
    assert plan.broken == []
    assert {m.issue for m in plan.moves} == {1234}


# ---------------------------------------------------------------------------
# Relative sibling link rewrite (the failure class the wave exists to prevent)
# ---------------------------------------------------------------------------


def _sibling_tree(root: Path) -> None:
    """A frame + spec for the SAME closed issue; the spec cites the frame via a
    relative *sibling* link (``../frames/…``, dropping the ``artifacts/`` prefix)."""
    _write(
        root / "artifacts" / "frames" / "2222-widget-frame.mdx",
        "---\nissue: 2222\n---\n# widget frame\n",
    )
    _write(
        root / "artifacts" / "specs" / "2222-widget-spec.mdx",
        "---\nissue: 2222\n---\n"
        "Promoted from [frame](../frames/2222-widget-frame.mdx).\n",
    )


def test_relative_sibling_ref_rewritten_and_still_resolves(tmp_path: Path) -> None:
    _sibling_tree(tmp_path)
    closed = _closed_file(tmp_path, [2222])
    rc, _out, err = _run(
        [
            "--root",
            str(tmp_path),
            "--month",
            "2026-07",
            "--closed-issues-file",
            str(closed),
            "--apply",
            "--no-git",
        ]
    )
    assert rc == 0, err
    frame_new = tmp_path / "artifacts/archive/2026-07/2222-widget-frame.mdx"
    spec_new = tmp_path / "artifacts/archive/2026-07/2222-widget-spec.mdx"
    assert frame_new.exists()
    assert spec_new.exists()
    text = spec_new.read_text(encoding="utf-8")
    # The stale sibling path is gone...
    assert "../frames/2222-widget-frame.mdx" not in text
    # ...and the rewritten link actually resolves to the frame's new location.
    m = re.search(r"\]\(([^)]+)\)", text)
    assert m is not None, text
    resolved = (spec_new.parent / m.group(1)).resolve()
    assert resolved == frame_new.resolve()


def test_residual_broken_flags_unrewritten_sibling(tmp_path: Path) -> None:
    """The 0-broken-links guarantee: a sibling ref left unrewritten IS detected
    (the old full-path-tail check was blind to ``../frames/…`` forms)."""
    old_rels = frozenset({"artifacts/frames/2222-widget-frame.mdx"})
    text = "Promoted from [frame](../frames/2222-widget-frame.mdx).\n"
    hits = _broken_refs_in_text(text, "artifacts/specs", old_rels)
    assert hits, "a dangling relative sibling ref must be flagged as broken"
    # A ref that does not point at a moved file is not flagged.
    assert _broken_refs_in_text(text, "artifacts/specs", frozenset()) == []


def test_sibling_ref_from_active_delta_is_rewritten(tmp_path: Path) -> None:
    """Sibling link whose target is a moved (closed) artifact but the referring
    file is a still-active delta: the link must be rewritten, not stranded."""
    _write(
        tmp_path / "artifacts" / "frames" / "3001-dep-frame.mdx",
        "---\nissue: 3001\n---\n# dep\n",
    )
    _write(
        tmp_path / "artifacts" / "analyses" / "3002-open-analysis.mdx",
        "---\nissue: 3002\n---\nSee [dep](../frames/3001-dep-frame.mdx).\n",
    )
    closed = _closed_file(tmp_path, [3001])  # 3002 stays open
    rc, _out, _ = _run(
        [
            "--root",
            str(tmp_path),
            "--month",
            "2026-07",
            "--closed-issues-file",
            str(closed),
            "--apply",
            "--no-git",
        ]
    )
    assert rc == 0
    analysis = (tmp_path / "artifacts/analyses/3002-open-analysis.mdx").read_text(
        encoding="utf-8"
    )
    assert "../frames/3001-dep-frame.mdx" not in analysis
    assert "../archive/2026-07/3001-dep-frame.mdx" in analysis
    assert (tmp_path / "artifacts/archive/2026-07/3001-dep-frame.mdx").exists()


# ---------------------------------------------------------------------------
# Date-prefixed basenames must not be mis-parsed as an issue number
# ---------------------------------------------------------------------------


def test_date_prefixed_basename_not_parsed_as_issue(tmp_path: Path) -> None:
    p = tmp_path / "artifacts" / "analyses" / "2026-07-03-doc-audit-strategy.claude.md"
    _write(p, "# doc audit strategy — no issue frontmatter\n")
    # The leading 2026 is a date, not issue #2026.
    assert _extract_issue(p) is None
    cands = {c.basename: c for c in iter_candidates(tmp_path)}
    assert cands["2026-07-03-doc-audit-strategy.claude.md"].issue is None
    # Even if #2026 were closed, the date-named delta must not be archived.
    plan = build_plan(tmp_path, "2026-07", {2026})
    assert plan.moves == []


def test_frontmatter_wins_over_date_prefix(tmp_path: Path) -> None:
    p = tmp_path / "artifacts" / "analyses" / "2026-07-03-late-analysis.md"
    _write(p, "---\nissue: 1999\n---\n# has real issue frontmatter\n")
    assert _extract_issue(p) == 1999
