"""Tests for multi-repo fetcher — RED phase (T7).

These tests drive the upcoming multi-repo behavior:
- run_fetch must iterate meta.repos[] (plural) rather than meta.repo (singular)
- issue keys in gh.json must be "owner/repo#N" strings, not bare integers
- fetch_dep_list must return IssueRef dicts {repo, issue} instead of bare ints
- cross-repo blocked_by references must be preserved across repos
- duplicate (repo, issue) pairs discovered via two label searches must be deduped

All 5 tests are expected to FAIL (RED) until T8/T9 land the multi-repo fetcher.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

from dep_graph.fetch import _derive_size_from_labels, _sanitize_milestone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _layout_file(tmp_path, *, repos):
    """Write a minimal valid multi-repo layout (meta.repos[] plural form)."""
    layout = {
        "meta": {
            "title": "T",
            "date": "2026-04-15",
            "repos": repos,
            "label_prefix": "graph:",
        },
        "lanes": [],
        "standalone": {"order": []},
        "overrides": {},
        "extra_deps": {"extra_blocked_by": {}, "extra_blocking": {}},
        "cross_deps": [],
        "title_rules": [],
    }
    p = tmp_path / "layout.json"
    p.write_text(json.dumps(layout))
    return p


def _patch_gh(monkeypatch):
    """Patch shutil.which so check_gh() does not abort the process."""
    monkeypatch.setattr("dep_graph.fetch.shutil.which", lambda _: "/usr/bin/gh")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_iterates_meta_repos(tmp_path, monkeypatch):
    """run_fetch must call `gh issue list --repo <R>` for every repo in meta.repos[]."""
    # Arrange
    from dep_graph.fetch import run_fetch

    layout = _layout_file(tmp_path, repos=["Roxabi/lyra", "Roxabi/roxabi-vault"])
    cache = tmp_path / "cache.gh.json"
    _patch_gh(monkeypatch)

    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        cp = MagicMock()
        cp.stdout = "[]"
        cp.stderr = ""
        cp.returncode = 0
        return cp

    monkeypatch.setattr("dep_graph.fetch.subprocess.run", fake_run)

    # Act
    run_fetch(layout, cache)

    # Assert — at least one gh issue list call per repo
    repo_args = [c[c.index("--repo") + 1] for c in calls if "--repo" in c]
    assert "Roxabi/lyra" in repo_args, (
        f"Roxabi/lyra not found in repo_args: {repo_args}"
    )
    assert "Roxabi/roxabi-vault" in repo_args, (
        f"Roxabi/roxabi-vault not found in repo_args: {repo_args}"
    )


def test_dedupes_same_issue_from_two_repos(tmp_path, monkeypatch):
    """If the same (repo, issue) appears in two label searches, gh.json has one entry."""
    # Arrange
    from dep_graph.fetch import run_fetch

    layout = _layout_file(tmp_path, repos=["Roxabi/lyra"])
    cache = tmp_path / "cache.gh.json"
    _patch_gh(monkeypatch)

    def fake_run(cmd, *a, **kw):
        cp = MagicMock()
        cp.stderr = ""
        cp.returncode = 0
        joined = " ".join(cmd)
        # Label-list commands return issue 641 twice (simulating overlap across two label queries)
        if "issue" in joined and "list" in joined and "--json" in joined:
            cp.stdout = "[641, 641]"
        else:
            cp.stdout = "[]"
        return cp

    monkeypatch.setattr("dep_graph.fetch.subprocess.run", fake_run)

    # Act
    run_fetch(layout, cache)

    # Assert — exactly one key for Roxabi/lyra#641 (not duplicated)
    data = json.loads(cache.read_text())
    keys_641 = [k for k in data.get("issues", {}) if k.endswith("#641")]
    assert len(keys_641) <= 1, f"Duplicate keys for #641: {keys_641}"


def test_extracts_issue_ref_from_dep_response(tmp_path, monkeypatch):
    """fetch_dep_list must extract {repo, issue} dicts from the gh api response."""
    # Arrange
    from dep_graph.fetch import fetch_dep_list

    dep_payload = json.dumps(
        [
            {"number": 703, "repository": {"full_name": "Roxabi/lyra"}},
            {"number": 24, "repository": {"full_name": "Roxabi/roxabi-vault"}},
        ]
    )

    def fake_run(cmd, *a, **kw):
        cp = MagicMock()
        cp.stdout = dep_payload
        cp.stderr = ""
        cp.returncode = 0
        return cp

    monkeypatch.setattr("dep_graph.fetch.subprocess.run", fake_run)

    # Act — real signature: fetch_dep_list(issue_num: int, direction: str, repo: str)
    result = fetch_dep_list(24, "blocked_by", "Roxabi/roxabi-vault")

    # Extract the dep list from the returned tuple (issue_num, direction, items)
    items = result[2]

    # Assert — items must be IssueRef dicts, not bare ints
    assert any(r == {"repo": "Roxabi/lyra", "issue": 703} for r in items), (
        f"Expected IssueRef {{repo: Roxabi/lyra, issue: 703}} in items: {items}"
    )
    assert any(r == {"repo": "Roxabi/roxabi-vault", "issue": 24} for r in items), (
        f"Expected IssueRef {{repo: Roxabi/roxabi-vault, issue: 24}} in items: {items}"
    )


def test_cross_repo_blocked_by_preserved(tmp_path, monkeypatch):
    """gh.json entry for vault#24 has blocked_by IssueRefs pointing at lyra#703."""
    # Arrange
    from dep_graph.fetch import run_fetch

    layout = _layout_file(tmp_path, repos=["Roxabi/lyra", "Roxabi/roxabi-vault"])
    cache = tmp_path / "cache.gh.json"
    _patch_gh(monkeypatch)

    def fake_run(cmd, *a, **kw):
        cp = MagicMock()
        cp.stderr = ""
        cp.returncode = 0
        joined = " ".join(cmd)

        if "/dependencies/blocked_by" in joined and "roxabi-vault" in joined.lower():
            # vault#24 is blocked by lyra#703
            cp.stdout = json.dumps(
                [{"number": 703, "repository": {"full_name": "Roxabi/lyra"}}]
            )
        elif "/dependencies/" in joined:
            cp.stdout = "[]"
        elif (
            "issue" in joined and "list" in joined and "roxabi-vault" in joined.lower()
        ):
            # vault has issue #24
            cp.stdout = "[24]"
        elif "issue" in joined and "list" in joined:
            # lyra has no labeled issues
            cp.stdout = "[]"
        elif (
            "/issues/" in joined
            and "roxabi-vault" in joined.lower()
            and "/dependencies" not in joined
        ):
            # issue meta for vault#24
            cp.stdout = json.dumps(
                {
                    "number": 24,
                    "title": "subscriber",
                    "state": "OPEN",
                    "labels": [],
                }
            )
        else:
            cp.stdout = "[]"
        return cp

    monkeypatch.setattr("dep_graph.fetch.subprocess.run", fake_run)

    # Act
    run_fetch(layout, cache)

    # Assert — "Roxabi/roxabi-vault#24" key exists with cross-repo blocked_by
    data = json.loads(cache.read_text())
    key = "Roxabi/roxabi-vault#24"
    assert key in data.get("issues", {}), (
        f"Missing key {key!r} in issues: {sorted(data.get('issues', {}).keys())}"
    )
    entry = data["issues"][key]
    assert any(
        r == {"repo": "Roxabi/lyra", "issue": 703} for r in entry.get("blocked_by", [])
    ), f"Expected cross-repo blocked_by in entry: {entry}"


def test_writes_gh_json_with_owner_repo_hash_keys(tmp_path, monkeypatch):
    """All keys in gh.json['issues'] must match the pattern owner/repo#N."""
    # Arrange
    from dep_graph.fetch import run_fetch

    layout = _layout_file(tmp_path, repos=["Roxabi/lyra"])
    cache = tmp_path / "cache.gh.json"
    _patch_gh(monkeypatch)

    def fake_run(cmd, *a, **kw):
        cp = MagicMock()
        cp.stderr = ""
        cp.returncode = 0
        joined = " ".join(cmd)

        if "issue" in joined and "list" in joined:
            cp.stdout = "[641]"
        elif "/issues/" in joined and "/dependencies" not in joined:
            cp.stdout = json.dumps(
                {
                    "number": 641,
                    "title": "x",
                    "state": "OPEN",
                    "labels": [],
                }
            )
        elif "/dependencies/" in joined:
            cp.stdout = "[]"
        else:
            cp.stdout = "[]"
        return cp

    monkeypatch.setattr("dep_graph.fetch.subprocess.run", fake_run)

    # Act
    run_fetch(layout, cache)

    # Assert — every key matches owner/repo#N
    data = json.loads(cache.read_text())
    issues = data.get("issues", {})
    assert issues, "gh.json['issues'] must not be empty"
    for k in issues:
        assert re.match(r"^[^/]+/[^/]+#\d+$", k), (
            f"Key {k!r} does not match owner/repo#N pattern"
        )


# ---------------------------------------------------------------------------
# _sanitize_milestone (#741 item 1)
# ---------------------------------------------------------------------------


def test_sanitize_milestone_allowlist_passes_realistic_names():
    assert _sanitize_milestone("v2.4.0 (alpha)") == "v2.4.0 (alpha)"
    assert _sanitize_milestone("Sprint #3") == "Sprint #3"
    assert _sanitize_milestone("Q2 2026 / Backend") == "Q2 2026 / Backend"
    assert _sanitize_milestone("M0") == "M0"


def test_sanitize_milestone_strips_html_tags():
    # < and > are stripped; / is in the allowlist so </script> → /script
    assert _sanitize_milestone("<script>xss</script>") == "scriptxss/script"


def test_sanitize_milestone_truncates_to_64_chars():
    assert _sanitize_milestone("x" * 100) == "x" * 64


def test_sanitize_milestone_none_on_empty_and_none():
    assert _sanitize_milestone(None) is None
    assert _sanitize_milestone("") is None
    assert _sanitize_milestone("   ") is None
    assert _sanitize_milestone("!!!") is None  # all dropped by allowlist


def test_sanitize_milestone_strips_trailing_leading_whitespace():
    assert _sanitize_milestone("  M0  ") == "M0"


# ---------------------------------------------------------------------------
# _derive_size_from_labels cap (#741 item 6)
# ---------------------------------------------------------------------------


def test_derive_size_caps_suffix_at_16_chars():
    # Unbounded label must not bloat cache — cap to 16 chars after 'size:' prefix
    long_label = "size:" + "x" * 100
    result = _derive_size_from_labels([long_label])
    assert result is not None
    assert len(result) == 16
    assert result == "x" * 16


def test_derive_size_short_labels_unchanged():
    assert _derive_size_from_labels(["size:S"]) == "S"
    assert _derive_size_from_labels(["size:F-lite"]) == "F-lite"
    assert _derive_size_from_labels(["other:X", "size:M"]) == "M"


def test_derive_size_none_when_absent():
    assert _derive_size_from_labels([]) is None
    assert _derive_size_from_labels(["foo", "bar"]) is None
