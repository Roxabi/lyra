"""Tests for multi-repo builder — RED phase (T10).

Golden test regeneration:
    Run `make dep-graph build` from the lyra repo root to regenerate the HTML
    fixture from the committed layout + gh.json cache.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from dep_graph.build import BuildPaths, run_build

FIXTURES = Path(__file__).parent / "fixtures"


def _write_layout(tmp_path, data):
    p = tmp_path / "layout.json"
    p.write_text(json.dumps(data))
    return p


def _write_cache(tmp_path, data):
    p = tmp_path / "cache.gh.json"
    p.write_text(json.dumps(data))
    return p


def _valid_single_lane_layout(*, repos, lane_order):
    """Minimal schema-valid multi-repo layout with one lane."""
    return {
        "meta": {
            "title": "T",
            "date": "2026-04-15",
            "repos": repos,
            "label_prefix": "graph:",
        },
        "lanes": [
            {
                "code": "a",
                "name": "A",
                "color": "a",
                "epic": {"issue": 1, "label": "x", "tag": "T"},
                "order": lane_order,
                "par_groups": {},
                "bands": [],
            }
        ],
        "standalone": {"order": []},
        "overrides": {},
        "extra_deps": {"extra_blocked_by": {}, "extra_blocking": {}},
        "cross_deps": [],
        "title_rules": [],
    }


def test_golden_html_byte_identical(tmp_path):
    """Build fed the committed lyra-layout-golden.* fixtures produces byte-identical HTML.

    To regenerate the golden fixture:
        make dep-graph build
    """
    layout_fx = FIXTURES / "lyra-layout-golden.layout.json"
    cache_fx = FIXTURES / "lyra-layout-golden.gh.json"
    html_fx = FIXTURES / "lyra-layout-golden.html"

    assert layout_fx.exists(), f"Missing fixture: {layout_fx}"
    assert cache_fx.exists(), f"Missing fixture: {cache_fx}"
    assert html_fx.exists(), f"Missing fixture: {html_fx}"

    out = tmp_path / "out.html"
    run_build(
        BuildPaths(
            layout_path=layout_fx, cache_path=cache_fx, out_path=out, bak_path=None
        )
    )

    expected = html_fx.read_text().splitlines(keepends=True)
    actual = out.read_text().splitlines(keepends=True)

    if expected != actual:
        diff = "".join(difflib.unified_diff(expected, actual, "expected", "actual"))
        raise AssertionError(f"Golden HTML drift:\n{diff}")


def test_repo_badge_on_foreign_card(tmp_path):
    """A card whose repo != meta.repos[0] gets a repo-badge in its HTML."""
    layout = _valid_single_lane_layout(
        repos=["Roxabi/lyra", "Roxabi/roxabi-vault"],
        lane_order=[
            {"repo": "Roxabi/lyra", "issue": 100},
            {"repo": "Roxabi/roxabi-vault", "issue": 24},
        ],
    )
    cache = {
        "fetched_at": "2026-04-15T00:00:00Z",
        "repos": ["Roxabi/lyra", "Roxabi/roxabi-vault"],
        "issues": {
            "Roxabi/lyra#100": {
                "repo": "Roxabi/lyra",
                "number": 100,
                "title": "native",
                "state": "OPEN",
                "labels": [],
                "blocked_by": [],
                "blocking": [],
            },
            "Roxabi/roxabi-vault#24": {
                "repo": "Roxabi/roxabi-vault",
                "number": 24,
                "title": "foreign",
                "state": "OPEN",
                "labels": [],
                "blocked_by": [],
                "blocking": [],
            },
        },
    }
    layout_path = _write_layout(tmp_path, layout)
    cache_path = _write_cache(tmp_path, cache)
    out = tmp_path / "out.html"

    run_build(
        BuildPaths(
            layout_path=layout_path, cache_path=cache_path, out_path=out, bak_path=None
        )
    )
    html = out.read_text()

    assert "repo-badge" in html, "Foreign card must render a repo-badge element"
    assert "roxabi-vault" in html, "Foreign card must show the foreign repo name"
    # Native lyra#100 should NOT have the badge (primary repo)
    # Rough check: count badge occurrences — should be 1 (only the foreign)
    assert html.count("repo-badge") == 1, (
        f"Expected 1 repo-badge, got {html.count('repo-badge')}"
    )


def test_not_found_placeholder(tmp_path, capsys):
    """IssueRef listed in layout but absent from gh.json renders a placeholder + stderr warn."""
    layout = _valid_single_lane_layout(
        repos=["Roxabi/lyra"],
        lane_order=[{"repo": "Roxabi/lyra", "issue": 999}],  # not in cache
    )
    cache = {
        "fetched_at": "2026-04-15T00:00:00Z",
        "repos": ["Roxabi/lyra"],
        "issues": {},  # empty — 999 is absent
    }
    layout_path = _write_layout(tmp_path, layout)
    cache_path = _write_cache(tmp_path, cache)
    out = tmp_path / "out.html"

    run_build(
        BuildPaths(
            layout_path=layout_path, cache_path=cache_path, out_path=out, bak_path=None
        )
    )
    html = out.read_text()

    assert (
        "missing" in html.lower()
        or "not-found" in html.lower()
        or "placeholder" in html.lower()
    ), "Placeholder card missing for unresolved IssueRef"
    captured = capsys.readouterr()
    assert "999" in captured.err or "Roxabi/lyra#999" in captured.err, (
        f"Expected stderr warning for missing issue, got: {captured.err!r}"
    )


def test_cross_repo_dep_arrow_rendered(tmp_path):
    """blocked_by IssueRef pointing to another repo in the merged pool → arrow/ref visible in HTML."""
    layout = _valid_single_lane_layout(
        repos=["Roxabi/lyra", "Roxabi/roxabi-vault"],
        lane_order=[
            {"repo": "Roxabi/lyra", "issue": 703},
            {"repo": "Roxabi/roxabi-vault", "issue": 24},
        ],
    )
    cache = {
        "fetched_at": "2026-04-15T00:00:00Z",
        "repos": ["Roxabi/lyra", "Roxabi/roxabi-vault"],
        "issues": {
            "Roxabi/lyra#703": {
                "repo": "Roxabi/lyra",
                "number": 703,
                "title": "ADR",
                "state": "OPEN",
                "labels": [],
                "blocked_by": [],
                "blocking": [{"repo": "Roxabi/roxabi-vault", "issue": 24}],
            },
            "Roxabi/roxabi-vault#24": {
                "repo": "Roxabi/roxabi-vault",
                "number": 24,
                "title": "subscriber",
                "state": "OPEN",
                "labels": [],
                "blocked_by": [{"repo": "Roxabi/lyra", "issue": 703}],
                "blocking": [],
            },
        },
    }
    layout_path = _write_layout(tmp_path, layout)
    cache_path = _write_cache(tmp_path, cache)
    out = tmp_path / "out.html"

    run_build(
        BuildPaths(
            layout_path=layout_path, cache_path=cache_path, out_path=out, bak_path=None
        )
    )
    html = out.read_text()

    # Must reference the cross-repo blocker — at minimum, the vault card shows #703 in its dep list
    assert "703" in html
    assert "24" in html
    # Ideally the HTML contains text like "← Roxabi/lyra#703" on the vault card — rough check
    assert "lyra#703" in html or "Roxabi/lyra" in html, (
        "Cross-repo dep ref not visible on vault card"
    )


def test_extra_blocked_by_renders_post_migration(tmp_path):
    """extra_deps.extra_blocked_by with owner/repo#N keys renders dep on the card."""
    layout = _valid_single_lane_layout(
        repos=["Roxabi/lyra"],
        lane_order=[
            {"repo": "Roxabi/lyra", "issue": 641},
            {"repo": "Roxabi/lyra", "issue": 640},
        ],
    )
    layout["extra_deps"] = {
        "extra_blocked_by": {"Roxabi/lyra#641": ["Roxabi/lyra#640"]},
        "extra_blocking": {},
    }
    cache = {
        "fetched_at": "2026-04-15T00:00:00Z",
        "repos": ["Roxabi/lyra"],
        "issues": {
            "Roxabi/lyra#641": {
                "repo": "Roxabi/lyra",
                "number": 641,
                "title": "child",
                "state": "OPEN",
                "labels": [],
                "blocked_by": [],
                "blocking": [],
            },
            "Roxabi/lyra#640": {
                "repo": "Roxabi/lyra",
                "number": 640,
                "title": "parent",
                "state": "OPEN",
                "labels": [],
                "blocked_by": [],
                "blocking": [],
            },
        },
    }
    layout_path = _write_layout(tmp_path, layout)
    cache_path = _write_cache(tmp_path, cache)
    out = tmp_path / "out.html"

    run_build(
        BuildPaths(
            layout_path=layout_path, cache_path=cache_path, out_path=out, bak_path=None
        )
    )
    html = out.read_text()

    # The #641 card must show #640 as a blocker in its dep row
    assert "640" in html, "extra_blocked_by dep #640 must appear in rendered HTML"
