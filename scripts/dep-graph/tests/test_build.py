"""Unit tests for dep_graph.build — _prepare_render_data standalone fallback.

Uses monkeypatch on dep_graph.build.derive_standalone_order (the imported name)
to observe call-site behavior without re-running derive logic.
"""

from __future__ import annotations

from dep_graph.build import _prepare_render_data

REPO = "Owner/repo"


def _minimal_layout(standalone: dict | None = None) -> dict:
    """Minimal layout dict with 1 stubbed lane and optional standalone block."""
    return {
        "lanes": [{"code": "a", "name": "A", "color": "red"}],
        "standalone": standalone if standalone is not None else {},
    }


def test_prepare_render_data_standalone_fallback_called(monkeypatch):
    """derive_standalone_order is called once when standalone has no order."""
    call_count = {"n": 0}

    def stub_derive_standalone_order(gh_issues, primary_repo):
        call_count["n"] += 1
        return []

    def stub_derive_lane(lane, gh_issues, primary_repo):
        return {**lane, "order": [], "par_groups": {}, "bands": []}

    monkeypatch.setattr(
        "dep_graph.build.derive_standalone_order", stub_derive_standalone_order
    )
    monkeypatch.setattr("dep_graph.build.derive_lane", stub_derive_lane)
    monkeypatch.setattr("dep_graph.build.inject_spacers", lambda x: x)
    monkeypatch.setattr(
        "dep_graph.build.flatten_lane", lambda lane, overrides, flag, gh_issues: {}
    )

    _prepare_render_data(_minimal_layout(), {}, REPO, {})

    assert call_count["n"] == 1


def test_prepare_render_data_standalone_fallback_skipped(monkeypatch):
    """derive_standalone_order is NOT called when standalone already has an order."""
    call_count = {"n": 0}

    def stub_derive_standalone_order(gh_issues, primary_repo):
        call_count["n"] += 1
        return []

    def stub_derive_lane(lane, gh_issues, primary_repo):
        return {**lane, "order": [], "par_groups": {}, "bands": []}

    monkeypatch.setattr(
        "dep_graph.build.derive_standalone_order", stub_derive_standalone_order
    )
    monkeypatch.setattr("dep_graph.build.derive_lane", stub_derive_lane)
    monkeypatch.setattr("dep_graph.build.inject_spacers", lambda x: x)
    monkeypatch.setattr(
        "dep_graph.build.flatten_lane", lambda lane, overrides, flag, gh_issues: {}
    )

    layout = _minimal_layout(standalone={"order": [{"repo": REPO, "issue": 1}]})
    _prepare_render_data(layout, {}, REPO, {})

    assert call_count["n"] == 0
