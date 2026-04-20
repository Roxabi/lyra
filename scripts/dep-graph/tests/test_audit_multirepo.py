"""Tests for multi-repo audit — RED phase (T11)."""

from __future__ import annotations

import json

from dep_graph.audit import run_audit


def _write_layout(tmp_path, data):
    p = tmp_path / "layout.json"
    p.write_text(json.dumps(data))
    return p


def _write_cache(tmp_path, data):
    p = tmp_path / "cache.gh.json"
    p.write_text(json.dumps(data))
    return p


def _layout_clean(repos, lane_refs):
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
                "order": lane_refs,
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


def test_audit_parses_owner_repo_hash_keys(tmp_path, monkeypatch):
    """Audit must not crash on new key format."""
    layout = _layout_clean(
        repos=["Roxabi/lyra"],
        lane_refs=[{"repo": "Roxabi/lyra", "issue": 641}],
    )
    cache = {
        "fetched_at": "2026-04-15T00:00:00Z",
        "repos": ["Roxabi/lyra"],
        "issues": {
            "Roxabi/lyra#641": {
                "repo": "Roxabi/lyra",
                "number": 641,
                "title": "x",
                "state": "OPEN",
                "labels": ["graph:lane/a"],
                "blocked_by": [],
                "blocking": [],
            },
        },
    }
    layout_path = _write_layout(tmp_path, layout)
    cache_path = _write_cache(tmp_path, cache)

    # Mock search_labeled_issues → layout & cache agree → clean
    def fake_search(repo, prefix, lane_codes):
        return {641}

    monkeypatch.setattr(
        "dep_graph.audit.search_labeled_issues", fake_search, raising=False
    )
    # If audit imports from fetch directly, patch there too
    monkeypatch.setattr(
        "dep_graph.fetch.search_labeled_issues", fake_search, raising=False
    )

    # Should not raise (parsing owner/repo#N keys is the contract)
    exit_code = run_audit(layout_path, cache_path)
    # Accept either exit 0 (clean) or non-raising error — just no crash on key parse
    assert exit_code in (0, 1), f"Unexpected exit: {exit_code}"


def test_audit_clean_on_migrated_layout(tmp_path, monkeypatch, capsys):
    """Labels + layout agree → audit exits 0 with a 'clean' result line."""
    layout = _layout_clean(
        repos=["Roxabi/lyra"],
        lane_refs=[{"repo": "Roxabi/lyra", "issue": 641}],
    )
    cache = {
        "fetched_at": "2026-04-15T00:00:00Z",
        "repos": ["Roxabi/lyra"],
        "issues": {
            "Roxabi/lyra#641": {
                "repo": "Roxabi/lyra",
                "number": 641,
                "title": "x",
                "state": "OPEN",
                "labels": ["graph:lane/a"],
                "blocked_by": [],
                "blocking": [],
            },
        },
    }
    layout_path = _write_layout(tmp_path, layout)
    cache_path = _write_cache(tmp_path, cache)

    def fake_search(repo, prefix, lane_codes):
        return {641}

    monkeypatch.setattr(
        "dep_graph.audit.search_labeled_issues", fake_search, raising=False
    )
    monkeypatch.setattr(
        "dep_graph.fetch.search_labeled_issues", fake_search, raising=False
    )

    exit_code = run_audit(layout_path, cache_path)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert (
        "clean" in out.lower() or "0 drift" in out.lower() or "no drift" in out.lower()
    ), f"Expected 'clean' signal in stdout; got: {out}"


def test_audit_drift_when_label_removed_per_repo(tmp_path, monkeypatch, capsys):
    """Label removed from one issue that is still in layout → exit 1, report owner/repo#N key."""
    # Layout lists vault#24 in lane/a — but vault#24 no longer has the graph:lane/a label
    layout = _layout_clean(
        repos=["Roxabi/lyra", "Roxabi/roxabi-vault"],
        lane_refs=[
            {"repo": "Roxabi/lyra", "issue": 641},
            {"repo": "Roxabi/roxabi-vault", "issue": 24},
        ],
    )
    cache = {
        "fetched_at": "2026-04-15T00:00:00Z",
        "repos": ["Roxabi/lyra", "Roxabi/roxabi-vault"],
        "issues": {
            "Roxabi/lyra#641": {
                "repo": "Roxabi/lyra",
                "number": 641,
                "title": "x",
                "state": "OPEN",
                "labels": ["graph:lane/a"],
                "blocked_by": [],
                "blocking": [],
            },
            "Roxabi/roxabi-vault#24": {
                "repo": "Roxabi/roxabi-vault",
                "number": 24,
                "title": "y",
                "state": "OPEN",
                "labels": [],  # label removed — drift
                "blocked_by": [],
                "blocking": [],
            },
        },
    }
    layout_path = _write_layout(tmp_path, layout)
    cache_path = _write_cache(tmp_path, cache)

    # Label search per repo: vault returns empty set (no #24 labeled), lyra returns {641}
    def fake_search(repo, prefix, lane_codes):
        return {641} if repo == "Roxabi/lyra" else set()

    monkeypatch.setattr(
        "dep_graph.audit.search_labeled_issues", fake_search, raising=False
    )
    monkeypatch.setattr(
        "dep_graph.fetch.search_labeled_issues", fake_search, raising=False
    )

    exit_code = run_audit(layout_path, cache_path)
    assert exit_code == 1, f"Expected drift exit 1, got {exit_code}"
    out = capsys.readouterr().out
    assert "Roxabi/roxabi-vault#24" in out or "roxabi-vault#24" in out, (
        f"Expected offending key in drift report; got: {out}"
    )
