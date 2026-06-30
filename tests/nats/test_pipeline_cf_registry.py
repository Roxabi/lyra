"""CF Pages project registry tests."""

from __future__ import annotations

from factory.nats.pipeline_cf_registry import (
    pages_branch_matches,
    resolve_pages_project,
    resolve_pages_repo,
)


def test_resolve_pages_project_default() -> None:
    entry = resolve_pages_project({"project_name": "roxabi-factory"})
    assert entry == {"repo": "Roxabi/roxabi-factory", "branch": "staging"}


def test_resolve_pages_repo() -> None:
    assert (
        resolve_pages_repo({"project_id": "roxabi-factory-dashboard"})
        == "Roxabi/roxabi-factory"
    )


def test_resolve_unknown_project() -> None:
    assert resolve_pages_project({"project_name": "no-such-project"}) is None


def test_pages_branch_matches() -> None:
    assert pages_branch_matches({"branch": "staging"}, "staging") is True
    assert pages_branch_matches({"branch": "main"}, "staging") is False
    assert pages_branch_matches(None, "staging") is True